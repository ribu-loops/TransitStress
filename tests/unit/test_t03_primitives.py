"""Handwritten references plus independent direct windows, without astronomy/network."""

import inspect
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from transitstress.config import load_config
from transitstress.eligibility import _enumerate_cases, support_evidence
from transitstress.errors import DataValidationError
from transitstress.injection import (
    box_mask,
    family_specs,
    inject_box,
    paired_inputs,
    retention_ratio,
)
from transitstress.masking import block_interval, make_observation_mask, mask_record
from transitstress.models import InjectionSpec, PreparedCurve, TimeMetadata
from transitstress.provenance import numeric_hash
from transitstress.support import window_layout, window_support


def curve(time=None, flux=None, **changes):
    t = np.array([0.0, 0.25, 0.5, 0.75, 1.0, 2.25, 2.5, 2.75] if time is None else time)
    values = dict(
        curve_id="independent-fixture",
        time_days=t,
        flux=np.ones(len(t)) if flux is None else flux,
        flux_err=np.full(len(t), 0.01),
        original_start_days=float(t[0]),
        original_end_days=float(t[-1]),
        bin_anchor_days=float(t[0]),
        support_counts=np.ones(len(t), dtype=int),
        time_metadata=TimeMetadata(None, "unknown", "unknown", "days", "fixture", 1.0),
        source_sha256="a" * 64,
    )
    return PreparedCurve(**(values | changes))


def test_production_injection_against_preexisting_handwritten_fixture():
    ref = json.loads(Path("tests/fixtures/injection_reference.json").read_text())
    c = curve(ref["time_days"])
    spec = InjectionSpec(
        "hand", ref["period_days"], ref["duration_days"], ref["epoch_days"], ref["depth"]
    )
    before = c.content_hash
    np.testing.assert_array_equal(box_mask(c.time_days, spec), ref["in_transit"])
    np.testing.assert_array_equal(inject_box(c, spec), ref["expected_flux_from_unity"])
    np.testing.assert_array_equal(inject_box(c, replace(spec, depth=0.0)), c.flux)
    for k in (-3, -1, 1, 3):
        np.testing.assert_array_equal(
            box_mask(c.time_days, replace(spec, epoch_days=0.5 + k * 2)), ref["in_transit"]
        )
    assert c.content_hash == before
    assert retention_ratio(0.0, 0.0).value is None
    assert retention_ratio(0.1, 0.0).reason == "zero_injected_depth"
    assert retention_ratio(-0.0015, 0.00075).value == -2
    assert retention_ratio(0.0015, 0.00075).value == 2
    with pytest.raises(DataValidationError, match="positive depth"):
        replace(spec, depth=0.0).validate_trial()


def test_additive_depth_not_multiplicative_no_clipping_or_noise():
    c = curve(flux=np.array([1.0, 2.0, 0.0005, 1.0, 1.0, 3.0, 4.0, 1.0]))
    spec = InjectionSpec("hand", 2.0, 0.5, 0.5, 0.00075)
    actual = inject_box(c, spec)
    np.testing.assert_array_equal(actual, [1.0, 1.99925, -0.00025, 1.0, 1.0, 2.99925, 3.99925, 1.0])
    assert not actual.flags.writeable


def test_half_open_boundaries_and_negative_phase():
    spec = InjectionSpec("binary-boundary", 2.0, 0.5, 0.5, 0.1)
    t = np.array([-0.0, 0.25, 0.75, 2.25, 2.75])
    np.testing.assert_array_equal(box_mask(t, spec), [False, True, False, True, False])
    np.testing.assert_array_equal(
        box_mask([np.nextafter(0.25, 0), 0.25, np.nextafter(0.75, 0), 0.75], spec),
        [False, True, True, False],
    )


def test_time_only_hand_counts_gap_strictness_endpoints_and_slices():
    time = [0.0, 0.125, 0.25, 0.75, 1.0, 2.0]
    layout = window_layout(time, 0.5, 0.5)
    np.testing.assert_array_equal(layout.neighbor_counts, [3, 3, 3, 2, 2, 1])
    np.testing.assert_array_equal(layout.segment_ids, [0, 0, 0, 0, 0, 1])
    np.testing.assert_array_equal(window_support([0.0, 0.5, 1.0], 4.0, 0.5), [3, 3, 3])
    np.testing.assert_array_equal(window_support([0.0, np.nextafter(0.5, 1.0)], 4.0, 0.5), [1, 1])
    assert not layout.left.flags.writeable
    assert set(inspect.signature(window_support).parameters) == {
        "time_days",
        "window_days",
        "segment_gap_days",
    }


def test_wrapped_transits_and_epoch_equivalence_with_separate_hand_counts():
    c = curve([-0.5, -0.25, 0.0, 0.25, 1.5, 1.75, 2.0, 2.25])
    spec = InjectionSpec("wrap", 2.0, 0.5, 0.0, 0.1)
    expected = [False, True, True, False, False, True, True, False]
    for epoch in (-4.0, 0.0, 4.0):
        np.testing.assert_array_equal(
            box_mask(c.time_days, replace(spec, epoch_days=epoch)), expected
        )


@pytest.mark.parametrize("width", [0.5, 1.5, 4.0])
def test_optimized_support_against_independent_bruteforce(width):
    t = np.array([0.0, 0.01, 0.04, 0.14, 0.25, 0.5, 1.25, 1.26, 1.49, 1.5, 1.75, 3.0])
    # Independently discover each point's contiguous segment by walking both ways.
    expected = []
    for i, center in enumerate(t):
        lo, hi = i, i
        while lo and t[lo] - t[lo - 1] <= 0.5:
            lo -= 1
        while hi + 1 < len(t) and t[hi + 1] - t[hi] <= 0.5:
            hi += 1
        expected.append(sum(abs(t[j] - center) <= width / 2 for j in range(lo, hi + 1)))
    layout = window_layout(t, width, 0.5)
    np.testing.assert_array_equal(layout.neighbor_counts, expected)
    for i in range(len(t)):
        assert all(abs(t[j] - t[i]) <= width / 2 for j in range(layout.left[i], layout.right[i]))


def test_masks_exact_random_stable_across_families_depths_pipelines():
    c = curve(np.linspace(0.0, 25.0, 2003))
    a = make_observation_mask(c, "random_missing", 20261019)
    assert a.removed_count == 400
    # The only RNG inputs are parent ID/master seed/purpose (literal independent seed tests in T01).
    for spec in family_specs(c):
        for _pipeline in ("rm_0p5d", "rm_1p5d"):
            b = make_observation_mask(c, "random_missing", 20261019)
            assert a.seed == b.seed and a.checksum == b.checksum
            pair = paired_inputs(c, b, spec)
            np.testing.assert_array_equal(pair.background_flux, c.flux[a.keep])
            np.testing.assert_array_equal(pair.flux_err, c.flux_err[a.keep])
            np.testing.assert_array_equal(pair.injected_flux, inject_box(c, spec)[a.keep])
            assert not pair.injected_flux.flags.writeable
    saved = mask_record(c, a)
    assert saved["keep_indices"] == np.flatnonzero(a.keep).tolist()
    assert saved["keep_indices_hash"] == numeric_hash({"keep_indices": np.flatnonzero(a.keep)})
    assert (
        make_observation_mask(replace(c, curve_id="other"), "random_missing", 20261019).seed
        != a.seed
    )
    assert make_observation_mask(c, "random_missing", 123).checksum != a.checksum
    np.testing.assert_array_equal(c.flux, np.ones(2003))


def test_central_block_elapsed_gaps_half_open_and_original_bounds():
    c = curve([0.0, 1.0, 4.0, 5.0, 6.0, 9.0, 10.0])
    mask = make_observation_mask(c, "central_block", 17)
    assert block_interval(c) == (4.0, 6.0)
    np.testing.assert_array_equal(mask.keep, [True, True, False, False, True, True, True])
    assert mask.removed_count == 2  # Not floor(20% * 7)=1.
    gapped = curve([0.0, 1.0, 3.0, 7.0, 9.0, 10.0])
    assert make_observation_mask(gapped, "central_block", 17).removed_count == 0
    bounded = curve(
        [0.1, 1.0, 4.0, 6.0, 9.9],
        original_start_days=0.0,
        original_end_days=10.0,
        bin_anchor_days=0.0,
    )
    assert block_interval(bounded) == (4.0, 6.0)


def test_all_masks_windows_and_isolated_points_preserved_in_exclusions():
    c = curve([0.0, 0.01, 0.02, 0.03, 0.04, 1.0, 2.0])
    config = load_config(Path("configs/protocol_v1.toml"))
    evidence = support_evidence(c, config)
    assert not evidence["passed"] and len(evidence["masks"]) == 3
    assert len(evidence["reasons"]) == 6
    for m in evidence["masks"]:
        assert len(m["windows"]) == 2
        for w in m["windows"]:
            # Random removal drops index 6 for this fixed parent seed; the
            # elapsed [.8,1.2) central block drops index 5, not index 6.
            expected = {"original": [5, 6], "random_missing": [5], "central_block": [6]}[
                m["definition"]
            ]
            assert not w["passed"] and w["failing_original_indices"] == expected
    assert len(family_specs(c)) == 9


def test_equal_observation_indices_still_preserve_distinct_planned_mask_cases():
    # A natural gap spans the entire central block: no central observations
    # exist to remove. Original and central are distinct planned conditions.
    time = np.r_[np.linspace(0.0, 9.99, 1000), np.linspace(15.0, 25.0, 1001)]
    curves = {str(i): curve(time, curve_id=f"gapped-{i}") for i in (1, 2)}
    for c in curves.values():
        original = make_observation_mask(c, "original", 20261019)
        block = make_observation_mask(c, "central_block", 20261019)
        assert original.checksum == block.checksum
    proposed = {
        "products": [
            {"tic_id": tic, "role": "development", "qc": {"prepared_hash": c.content_hash}}
            for tic, c in curves.items()
        ]
    }
    cases = _enumerate_cases(
        proposed, curves, load_config(Path("configs/protocol_v1.toml")), "development"
    )
    assert len(cases) == len({c["case_id"] for c in cases}) == 54
    assert {
        name: sum(c["mask_definition"] == name for c in cases)
        for name in ("original", "random_missing", "central_block")
    } == {"original": 18, "random_missing": 18, "central_block": 18}
