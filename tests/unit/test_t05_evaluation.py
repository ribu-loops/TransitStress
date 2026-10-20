"""Hand-derived expected geometry, coverage, retention and denominator fixtures."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from transitstress.errors import DataValidationError
from transitstress.evaluation import (
    coverage,
    evaluate_control,
    evaluate_injection,
    paired_retention,
    summarize_injections,
)
from transitstress.models import (
    InjectionSpec,
    ObservationMask,
    PreparedCurve,
    SearchResult,
    TimeMetadata,
)
from transitstress.provenance import canonical_json

REFERENCE = json.loads(Path("tests/fixtures/t05_reference.json").read_text())
SHA = "a" * 64


def curve(time=None, start=0.0, end=5.0):
    time = np.array(REFERENCE["time_days"] if time is None else time, dtype=float)
    return PreparedCurve(
        "evaluator-only",
        time,
        np.ones(len(time)),
        np.full(len(time), 0.001),
        start,
        end,
        start,
        np.ones(len(time), dtype=int),
        TimeMetadata(2457000.0, "jd", "tdb", "d", "e-/s", 123.0),
        SHA,
    )


def spec(period=2.0, duration=0.5, epoch=0.5, depth=0.001):
    return InjectionSpec("handwritten", period, duration, epoch, depth)


def peak(period=2.0, epoch=0.5):
    return SearchResult("ok", period, epoch, 0.125, 0.001, 0.0001, 10.0, 1.0, SHA, 0.1)


def failed():
    return SearchResult(
        "error", None, None, None, None, None, None, None, SHA, 0.1, "fixture_failure"
    )


def original(c):
    return ObservationMask(np.ones(len(c.time_days), dtype=bool), "original", None)


def evaluate(c=None, truth=None, search=None, background=None, mask=None):
    c = c or curve()
    return evaluate_injection(c, mask or original(c), truth or spec(), search or peak(), background)


def test_handwritten_coverage_and_half_open_edges_use_original_bounds():
    c = curve()
    result = coverage(c, original(c), spec())
    assert result.full_centers_days == tuple(REFERENCE["full_centers_days"])
    assert result.full_event_points == tuple(REFERENCE["full_event_points"])
    assert result.total_in_transit_points == 12
    assert result.eligible and result.partial_centers_days == ()
    # Original endpoints have no samples after masking: baseline is still [0,5].
    keep = np.ones(len(c.time_days), dtype=bool)
    keep[[0, -1]] = False
    result = coverage(c, ObservationMask(keep, "central_block", None), spec())
    assert result.full_centers_days == (0.5, 2.5, 4.5)
    assert result.full_event_points == (4, 4, 4)


def test_partial_edge_events_counts_remain_separate_even_when_empty():
    c = curve([0, 0.125, 1, 1.75, 1.875, 2, 2.125, 3, 3.75, 3.875, 4], end=4.0)
    result = coverage(c, original(c), spec(epoch=0.0))
    assert result.full_centers_days == (2.0,)
    assert result.full_event_points == (4,)
    assert result.partial_centers_days == (0.0, 4.0)
    assert result.partial_event_points == (2, 3)
    assert result.total_in_transit_points == 9 and not result.eligible


def test_three_per_event_and_two_events_are_separate_from_total_point_count():
    c = curve([0.375, 0.5, 2.375, 2.5, 4.375, 4.5])
    sparse = coverage(c, original(c), spec())
    assert sparse.total_in_transit_points == 6
    assert sparse.full_event_points == (2, 2, 2)
    assert not sparse.eligible and sparse.observed_centers_days == ()
    c = curve([0.375, 0.5, 0.625, 2.375, 2.5, 2.625])
    minimum = coverage(c, original(c), spec())
    assert minimum.total_in_transit_points == 6
    assert minimum.observed_centers_days == (0.5, 2.5) and minimum.eligible


def test_half_duration_timing_boundary_is_inclusive_and_period_rule_is_required():
    boundary = evaluate(search=peak(epoch=0.75))
    assert boundary.maximum_timing_error_days.value == 0.25 and boundary.exact_recovery
    beyond = evaluate(search=peak(epoch=0.750001))
    assert not beyond.exact_recovery
    # Half period shares every true center, but must fail primary period rule.
    half = evaluate(search=peak(period=1.0))
    assert half.maximum_timing_error_days.value == 0 and not half.exact_recovery


@pytest.mark.parametrize("epoch", [0.5, 2.5, -3.5, 200.5])
def test_integer_period_epoch_shifts_pass(epoch):
    result = evaluate(search=peak(epoch=epoch), background=peak())
    assert result.status == "exact" and result.exact_recovery
    assert result.maximum_timing_error_days.value == 0
    assert result.background_already_matches is True
    assert result.exact_after_injection_without_background_match is False
    shifted_truth = evaluate(truth=spec(epoch=epoch), background=peak())
    assert shifted_truth.full_centers_days == (0.5, 2.5, 4.5)
    assert shifted_truth.exact_recovery


def test_old_independent_reference_is_scored_by_production_evaluator():
    ref = json.loads(Path("tests/fixtures/evaluation_reference.json").read_text())
    for name in ("good_fit", "wrong_phase_fit"):
        expected = ref[name]
        result = evaluate(search=peak(expected["period_days"], expected["epoch_days"]))
        assert result.exact_recovery is expected["exact"]
        assert result.relative_period_error.value == expected["relative_period_error"]
        assert result.maximum_timing_error_days.value == expected["maximum_timing_error_days"]


def test_accumulated_drift_fails_despite_sub_one_percent_period_error():
    ref = REFERENCE["drift"]
    # Three literal offsets per independently listed true center; no injection code.
    time = sorted(
        [0.0, 30.0] + [c + d for c in ref["centers_days"] for d in (-0.03125, 0, 0.03125)]
    )
    result = evaluate(curve(time, end=30.0), spec(duration=0.125), peak(2.018))
    assert result.relative_period_error.value == pytest.approx(0.009)
    assert result.maximum_timing_error_days.value == pytest.approx(0.252)
    assert result.exact_recovery is False and result.status == "other_peak"


@pytest.mark.parametrize("period,status", [(1.0, "half_period_peak"), (4.0, "double_period_peak")])
def test_aliases_are_period_only_secondary_peaks(period, status):
    result = evaluate(search=peak(period, epoch=1.0))
    assert result.status == status and result.exact_recovery is False
    assert result.half_period_peak is (period == 1.0)
    assert result.double_period_peak is (period == 4.0)


def test_searchability_is_based_on_independent_declared_bounds():
    assert evaluate(truth=spec(period=0.8, duration=0.125)).half_alias_searchable is False
    assert evaluate(truth=spec(period=6.0)).double_alias_searchable is False
    assert evaluate(truth=spec(period=1.5)).half_alias_searchable is True  # .75 inclusive
    assert evaluate(truth=spec(period=5.0)).double_alias_searchable is True  # 10 inclusive


def test_insufficient_coverage_and_failed_searches_preserve_diagnostics():
    c = curve()
    keep = np.zeros(len(c.time_days), dtype=bool)
    keep[REFERENCE["remove_two_events_keep_indices"]] = True
    mask = ObservationMask(keep, "central_block", None)
    result = evaluate(c, mask=mask, background=peak())
    assert result.status == "insufficient_coverage" and result.exact_recovery is None
    assert result.full_event_points == (4, 0, 0) and result.observed_transits == 1
    assert result.relative_period_error.value == 0 and result.maximum_timing_error_days.value == 0
    assert result.background_already_matches is None
    assert result.background_match_reason == "insufficient_coverage"
    technical = evaluate(c, search=failed(), mask=mask)
    assert technical.status == "error" and technical.coverage_eligible is False
    assert technical.relative_period_error.reason == "search_error"
    empty = ObservationMask(np.zeros(len(c.time_days), dtype=bool), "central_block", None)
    assert evaluate(c, mask=empty).maximum_timing_error_days.reason == "no_observed_full_events"


def test_background_coincidence_is_per_injection_and_does_not_change_primary_count():
    # Both ephemerides need observed full events; the sparse edge fixture above
    # intentionally has only two samples in the shifted windows.
    c = curve(np.arange(0.0, 5.01, 0.0625))
    matched = evaluate(c, background=peak())
    other_ephemeris = evaluate(c, truth=spec(epoch=1.0), search=peak(epoch=1.0), background=peak())
    assert matched.exact_recovery is other_ephemeris.exact_recovery is True
    assert matched.background_already_matches is True
    assert other_ephemeris.background_already_matches is False
    assert other_ephemeris.exact_after_injection_without_background_match is True
    for background, reason in [
        (None, "background_search_unavailable"),
        (failed(), "background_search_error"),
    ]:
        result = evaluate(background=background)
        assert result.exact_recovery and result.background_already_matches is None
        assert result.exact_after_injection_without_background_match is None
        assert result.background_match_reason == reason
    with pytest.raises(DataValidationError, match="background grid"):
        evaluate(background=replace(peak(), grid_hash="b" * 64))


def test_paired_retention_ordinary_means_can_exceed_one_or_be_negative():
    ref = REFERENCE["retention"]
    c = curve(ref["time_days"])
    mask, truth = original(c), spec()
    result = paired_retention(c, mask, truth, c.time_days, ref["z0"], c.time_days, ref["z1"])
    assert result.retained_depth.value == pytest.approx(0.002)
    assert result.ratio.value == pytest.approx(2.0)
    reverse = paired_retention(c, mask, truth, c.time_days, ref["z1"], c.time_days, ref["z0"])
    assert reverse.ratio.value == pytest.approx(-2.0)
    zero = paired_retention(
        c, mask, spec(depth=0.0), c.time_days, ref["z0"], c.time_days, ref["z0"]
    )
    assert zero.retained_depth.value == 0.0 and zero.ratio.reason == "zero_injected_depth"


@pytest.mark.parametrize(
    "time,reason",
    [
        ([0.0, 1.0, 2.0], "insufficient_retention_in_points"),
        ([0.25, 0.5, 0.625], "insufficient_retention_out_points"),
    ],
)
def test_undefined_retention_has_null_reason(time, reason):
    c = curve(time)
    result = paired_retention(
        c, original(c), spec(), time, np.ones(len(time)), time, np.ones(len(time))
    )
    assert result.retained_depth.value is result.ratio.value is None
    assert result.retained_depth.reason == reason


def test_missing_processing_and_misalignment_are_distinct():
    c = curve()
    result = paired_retention(c, original(c), spec(), c.time_days, None, c.time_days, c.flux)
    assert result.ratio.reason == "paired_processing_unavailable"
    with pytest.raises(DataValidationError, match="retention time"):
        paired_retention(c, original(c), spec(), c.time_days, c.flux, c.time_days[::-1], c.flux)
    with pytest.raises(DataValidationError, match="retention flux"):
        paired_retention(c, original(c), spec(), c.time_days, c.flux[:-1], c.time_days, c.flux)


def test_both_control_kinds_have_no_recovery_truth_even_when_search_fails():
    for search in (peak(), failed()):
        result = evaluate_control(search)
        assert result.scientific_outcome == "not_applicable"
        assert result.exact_recovery is result.coverage_eligible is result.full_events is None
        assert result.total_in_transit_points is result.observed_transits is None
        assert result.half_period_peak is result.background_already_matches is None
        assert result.retention_ratio.reason == "no_injection_truth"
        assert result.status == ("control" if search.status == "ok" else "error")


def test_summary_retains_denominators_failures_and_background_breakout():
    c = curve()
    empty = ObservationMask(np.zeros(len(c.time_days), dtype=bool), "central_block", None)
    results = [
        evaluate(background=peak()),
        evaluate(background=peak(epoch=1.0)),
        evaluate(),
        evaluate(search=peak(epoch=1.0)),
        evaluate(mask=empty),
        evaluate(search=failed()),
    ]
    rows = {(str(i), "rm_0p5d"): result for i, result in enumerate(results)}
    summary = summarize_injections(rows, tuple(rows))["rm_0p5d"]
    assert summary == dict(
        planned=6,
        coverage_eligible=5,
        exact=3,
        insufficient_coverage=1,
        technical_failures=1,
        exact_with_background_match=1,
        exact_without_background_match=1,
        exact_background_unknown=1,
    )
    saved = json.loads(canonical_json(dict(results=list(rows.values()), summary=summary)))
    assert saved["results"][-1]["relative_period_error"] == {
        "value": None,
        "reason": "search_error",
    }
    assert saved["summary"] == summary
    with pytest.raises(DataValidationError, match="missing/extra"):
        summarize_injections(dict(list(rows.items())[:-1]), tuple(rows))
    with pytest.raises(DataValidationError, match="duplicate"):
        summarize_injections(rows, tuple(rows) * 2)
