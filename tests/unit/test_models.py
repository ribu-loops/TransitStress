"""Contract invariants and independent malformed inputs, with no network/UI."""

from dataclasses import replace

import numpy as np
import pytest

from transitstress.errors import DataValidationError
from transitstress.models import (
    DetrendConfig,
    EvaluationResult,
    InjectionSpec,
    Measurement,
    ObservationMask,
    PeriodGrid,
    PreparedCurve,
    RunManifest,
    SearchResult,
    TimeMetadata,
)

SHA = "a" * 64


def curve(**changes):
    values = dict(
        curve_id="fixture",
        time_days=np.array([0.0, 1.0, 2.0]),
        flux=np.ones(3),
        flux_err=np.full(3, 0.01),
        original_start_days=0.0,
        original_end_days=2.0,
        bin_anchor_days=0.0,
        support_counts=np.array([2, 3, 2]),
        time_metadata=TimeMetadata(None, "unknown", "unknown", "days", "relative", 1.0),
        source_sha256=SHA,
    )
    return PreparedCurve(**(values | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"time_days": [0.0, 0.0, 2.0]},
        {"time_days": [1.0, 0.0, 2.0]},
        {"time_days": [0.0, float("nan"), 2.0]},
        {"flux": [1.0, -1.0, 1.0]},
        {"flux": [1.0, float("inf"), 1.0]},
        {"flux_err": [1.0, -1.0, 1.0]},
        {"flux_err": [1.0, 0.0, 1.0]},
        {"flux_err": [1.0, 1e-300, 1.0]},
        {"flux_err": [1.0, 1e300, 1.0]},
        {"flux_err": [1.0, 1.0]},
        {"flux": [[1.0, 1.0, 1.0]]},
        {"support_counts": [1.0, 1.0, 1.0]},
        {"support_counts": [1, 0, 1]},
        {"support_counts": [1, 1]},
        {"original_end_days": 1.0},
        {"bin_anchor_days": 1.0},
        {"source_sha256": "short"},
        {"flux": [True, True, True]},
        {"flux": ["1", "1", "1"]},
    ],
)
def test_curve_rejects_invalid_inputs(changes):
    with pytest.raises(DataValidationError):
        curve(**changes)


def test_copy_and_readonly_do_not_mutate_caller():
    time = np.array([0.0, 1.0, 2.0])
    flux = np.ones(3)
    error = np.full(3, 0.01)
    support = np.array([2, 3, 2])
    original = [array.copy() for array in (time, flux, error, support)]
    prepared = curve(time_days=time, flux=flux, flux_err=error, support_counts=support)
    identity = prepared.content_hash
    for source, expected in zip((time, flux, error, support), original, strict=True):
        np.testing.assert_array_equal(source, expected)
    flux[0] = 99
    support[0] = 99
    assert prepared.flux[0] == 1 and prepared.support_counts[0] == 2
    assert prepared.content_hash == identity
    for name in ("time_days", "flux", "flux_err", "support_counts"):
        with pytest.raises(ValueError):
            getattr(prepared, name)[0] = 42


@pytest.mark.parametrize(
    "changes",
    [
        {"period_days": 0},
        {"duration_days": 2},
        {"depth": -1},
        {"depth": float("nan")},
        {"epoch_days": float("inf")},
        {"depth": True},
        {"depth": 10**1000},
    ],
)
def test_bad_injection_spec(changes):
    values = dict(
        family_id="fixture", period_days=2.0, duration_days=0.5, epoch_days=0.5, depth=0.00075
    )
    with pytest.raises(DataValidationError):
        InjectionSpec(**(values | changes))


def test_zero_helper_and_workbench_limits():
    spec = InjectionSpec("fixture", 2.0, 0.125, 0.5, 0.0)
    assert spec.depth_ppm == 0
    with pytest.raises(DataValidationError, match="positive depth"):
        spec.validate_trial()
    replace(spec, depth=0.00075).validate_trial(workbench=True)
    with pytest.raises(DataValidationError):
        replace(spec, depth=0.00075, period_days=9).validate_trial(workbench=True)


def test_masks_alignment_hash_and_seed_contract():
    keep = np.array([True, False, True])
    mask = ObservationMask(keep, "random_missing", 5)
    keep[:] = True
    assert mask.removed_count == 1
    assert len(mask.checksum) == 64
    mask.validate_alignment(curve())
    with pytest.raises(DataValidationError):
        ObservationMask(np.array([True]), "original", None).validate_alignment(curve())
    for keep, name, seed in [
        ([1, 0], "random_missing", 1),
        ([True, False], "original", None),
        ([True], "central_block", 1),
        ([True], "random_missing", None),
    ]:
        with pytest.raises(DataValidationError):
            ObservationMask(np.array(keep), name, seed)


def test_detrend_and_grid_contracts():
    DetrendConfig("rm_0p5d", 0.5)
    with pytest.raises(DataValidationError):
        DetrendConfig("rm_0p5d", 1.5)
    grid = PeriodGrid(np.array([0.75, 1.0, 10.0]), 25.0, SHA)
    assert len(grid.checksum) == 64
    for periods in ([1.0, 1.0], [0.5, 1.0], [1.0, 11.0], [float("nan")]):
        with pytest.raises(DataValidationError):
            PeriodGrid(np.array(periods), 25.0, SHA)
    with pytest.raises(DataValidationError):
        PeriodGrid(np.linspace(0.75, 10.0, 50001), 25.0, SHA)


def test_missing_measurement_needs_reason_and_preserves_negative_retention():
    assert Measurement(-0.5).value == -0.5
    assert Measurement(None, "zero_injected_depth").value is None
    for value, reason in [(None, None), (float("nan"), None), (1.0, "missing")]:
        with pytest.raises(DataValidationError):
            Measurement(value, reason)


def test_search_failure_has_no_fake_metrics():
    result = SearchResult(
        "error", None, None, None, None, None, None, None, SHA, 0.1, "invalid_trend"
    )
    with pytest.raises(DataValidationError):
        replace(result, period_days=3.0)
    good = SearchResult("ok", 2.0, 0.5, 0.125, 0.001, 0.0001, 10.0, 100.0, SHA, 0.2)
    with pytest.raises(DataValidationError):
        replace(good, power=float("nan"))


def test_evaluation_status_and_null_contract():
    missing = Measurement(None, "not_evaluated")
    result = EvaluationResult(
        "control",
        0,
        0,
        0,
        None,
        missing,
        missing,
        None,
        None,
        None,
        None,
        missing,
        missing,
        None,
        None,
        "no_injection_truth",
    )
    with pytest.raises(DataValidationError):
        replace(result, exact_recovery=True)
    with pytest.raises(DataValidationError):
        replace(result, retention_ratio=float("nan"))


def test_manifest_contract_utc_counts_and_provenance():
    manifest = RunManifest(
        1,
        "transitstress-v1",
        SHA,
        SHA,
        SHA,
        SHA,
        "b" * 40,
        "2026-10-19T10:00:00+00:00",
        None,
        "fixture",
        ("fixture-only",),
        "development",
        156,
        0,
        "incomplete",
        (),
    )
    for changes in [
        {"status": "complete"},
        {"actual_rows": 157},
        {"expected_rows": 1001},
        {"started_utc": "2026-10-19T10:00:00"},
        {"started_utc": None},
        {"finished_utc": "2026-10-19T09:00:00Z"},
        {"config_hash": "short"},
    ]:
        with pytest.raises(DataValidationError):
            replace(manifest, **changes)
    replace(
        manifest,
        status="complete",
        actual_rows=156,
        finished_utc="2026-10-19T11:00:00Z",
        file_checksums=(("results.json", SHA),),
    )


def test_ragged_masks_and_invalid_metadata_enums_are_controlled():
    with pytest.raises(DataValidationError):
        ObservationMask([[True], [True, False]], "original", None)
    with pytest.raises(DataValidationError):
        TimeMetadata(None, [], "unknown", "days", "relative", 1.0)
    assert InjectionSpec("helper", 2.0, 0.5, 0.5, 1.0).depth == 1.0
