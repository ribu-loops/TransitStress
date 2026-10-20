"""Independent direct medians and original-baseline grid arithmetic."""

from dataclasses import replace

import numpy as np
import pytest

from transitstress.config import SearchConfig
from transitstress.detrending import detrend
from transitstress.errors import DataValidationError
from transitstress.models import DetrendConfig, PreparedCurve, TimeMetadata
from transitstress.provenance import canonical_hash
from transitstress.search import build_search_grid, search_bls


def curve(time=None, **changes):
    time = np.linspace(0, 30, 3001) if time is None else np.asarray(time, dtype=float)
    values = dict(
        curve_id="independent-t04-fixture",
        time_days=time,
        flux=np.ones(len(time)),
        flux_err=np.full(len(time), 0.001),
        original_start_days=0.0,
        original_end_days=30.0,
        bin_anchor_days=0.0,
        support_counts=np.ones(len(time), dtype=int),
        time_metadata=TimeMetadata(None, "unknown", "unknown", "days", "fixture", 1.0),
        source_sha256="a" * 64,
    )
    return PreparedCurve(**(values | changes))


@pytest.mark.parametrize("window,pipeline", [(0.5, "rm_0p5d"), (1.5, "rm_1p5d")])
def test_medians_against_direct_irregular_endpoint_gap_reference(window, pipeline):
    # Exact binary endpoints, an exact 0.5-day nonsplit and a >0.5-day gap.
    t = np.r_[np.arange(17) / 64, 0.75 + np.arange(17) / 64, 2 + np.arange(17) / 64]
    f = 1 + np.arange(len(t)) % 9 / 10
    f[3] = -0.2  # Negative additive dips are preserved, not clipped.
    e = np.full(len(t), 0.02)
    before = [a.copy() for a in (t, f, e)]
    groups = np.cumsum(np.r_[False, np.diff(t) > 0.5])
    expected = np.array(
        [
            np.median(f[(groups == groups[i]) & (np.abs(t - center) <= window / 2)])
            for i, center in enumerate(t)
        ]
    )
    result = detrend(t, f, e, DetrendConfig(pipeline, window))
    np.testing.assert_array_equal(result.trend, expected)
    np.testing.assert_array_equal(result.flux, f / expected)
    np.testing.assert_array_equal(result.flux_err, e / expected)
    np.testing.assert_array_equal(result.segment_ids, groups)
    for original, saved in zip((t, f, e), before, strict=True):
        np.testing.assert_array_equal(original, saved)
    assert result.flux[3] < 0
    assert not result.trend.flags.writeable


def test_constant_flux_and_error_division_no_renormalization():
    t = np.arange(101) / 100
    for config in (DetrendConfig("rm_0p5d", 0.5), DetrendConfig("rm_1p5d", 1.5)):
        result = detrend(t, np.full(101, 2.0), np.full(101, 0.04), config)
        np.testing.assert_array_equal(result.trend, np.full(101, 2.0))
        np.testing.assert_array_equal(result.flux, np.ones(101))
        np.testing.assert_array_equal(result.flux_err, np.full(101, 0.02))


def test_window_failure_visible_and_no_cross_segment_borrowing():
    with pytest.raises(DataValidationError, match="five neighbors"):
        detrend([0, 0.01, 0.02, 0.03, 1], np.ones(5), np.ones(5), DetrendConfig("rm_1p5d", 1.5))
    t = np.r_[np.arange(10) / 100, 1 + np.arange(10) / 100]
    f = np.r_[np.ones(10), np.full(10, 4.0)]
    np.testing.assert_array_equal(
        detrend(t, f, np.ones(20), DetrendConfig("rm_1p5d", 1.5)).trend, f
    )


def test_grid_independent_formula_original_bounds_not_occupied_endpoints():
    c = curve(np.linspace(0.1, 29.9, 3001))
    grid = build_search_grid(c, SearchConfig())
    # Hand arithmetic: df=(1/24)/30^2=1/21600; 26640 frequency intervals.
    # Compare in frequency, where the linear grid is well conditioned.
    # Reciprocal near the upper period magnifies subtraction roundoff.
    expected_frequency = 4 / 3 - np.arange(26641) / 21600
    assert len(grid.periods_days) == 26641
    np.testing.assert_allclose(
        1 / grid.periods_days, expected_frequency, rtol=0, atol=2 * np.finfo(float).eps
    )
    assert grid.periods_days[-1] == pytest.approx(10.0, abs=3e-14)
    assert grid.original_baseline_days == 30
    assert grid.search_config_hash == canonical_hash(SearchConfig())
    assert (
        grid.checksum
        != build_search_grid(replace(c, original_end_days=31.0), SearchConfig()).checksum
    )
    assert grid.checksum == build_search_grid(replace(c, flux=c.flux * 2), SearchConfig()).checksum


@pytest.mark.parametrize("mode", ["tie", "nonfinite_peak", "no_finite"])
def test_peak_selection_and_visible_failures(monkeypatch, mode):
    c = curve()
    grid = build_search_grid(c, SearchConfig())
    n = len(grid.periods_days)
    power = np.full(n, np.nan)
    power[2], power[10] = (9.0, 9.0) if mode != "no_finite" else (np.nan, np.nan)
    depth = np.full(n, 0.01)
    if mode == "nonfinite_peak":
        depth[2] = np.nan
    raw = dict(
        period=grid.periods_days,
        power=power,
        depth=depth,
        depth_err=np.full(n, 0.001),
        depth_snr=np.full(n, 10.0),
        transit_time=np.full(n, 0.2),
        duration=np.full(n, 0.125),
    )
    monkeypatch.setattr("transitstress.search.BoxLeastSquares.power", lambda *a, **k: raw)
    out = search_bls(c.time_days, c.flux, c.flux_err, grid, SearchConfig())
    assert len(out.periodogram["power"]) == n
    if mode == "tie":
        assert out.result.period_days == grid.periods_days[2]
    else:
        assert out.result.status == "error" and out.result.period_days is None
        assert out.result.failure_reason == (
            "no_finite_BLS_power" if mode == "no_finite" else "nonfinite_strongest_peak_diagnostics"
        )
