"""Post-search truth scoring only. This module cannot execute a blind search."""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike

from transitstress.config import EvaluationConfig, SearchConfig
from transitstress.errors import DataValidationError
from transitstress.models import (
    EvaluationResult,
    InjectionSpec,
    Measurement,
    ObservationMask,
    PreparedCurve,
    SearchResult,
    _source_array,
    _vector,
)


@dataclass(frozen=True)
class Coverage:
    full_centers_days: tuple[float, ...]
    full_event_points: tuple[int, ...]
    observed_centers_days: tuple[float, ...]
    partial_centers_days: tuple[float, ...]
    partial_event_points: tuple[int, ...]
    total_in_transit_points: int
    eligible: bool


def _windows(
    curve: PreparedCurve, spec: InjectionSpec
) -> tuple[tuple[float, float, float, bool], ...]:
    """Bound enumeration before allocation; reduce whole-period epoch offsets.

    Original bounds are closed observation bounds. Windows are half-open; a
    window starting at the last bound can contain that final observation.
    Partial events are saved even when they have no retained observations.
    """
    start, end = curve.original_start_days, curve.original_end_days
    period, half = spec.period_days, spec.duration_days / 2
    epoch = spec.epoch_days % period
    low, high = (start - half - epoch) / period, (end + half - epoch) / period
    if not all(math.isfinite(x) for x in (low, high)) or half == 0:
        raise DataValidationError("coverage events: unrepresentable; use representable days")
    first, last = math.floor(low), math.ceil(high)
    if last - first + 1 > 50000:
        raise DataValidationError("coverage events: exceed 50000; use a bounded period/baseline")
    windows = []
    for k in range(first, last + 1):
        center = epoch + k * period
        left, right = center - half, center + half
        if not all(math.isfinite(x) for x in (center, left, right)) or left >= right:
            raise DataValidationError("coverage window: unrepresentable; use relative days")
        if right > start and left <= end:
            windows.append((center, left, right, left >= start and right <= end))
    return tuple(windows)


def coverage(
    curve: PreparedCurve,
    mask: ObservationMask,
    spec: InjectionSpec,
    config: EvaluationConfig = EvaluationConfig(),
) -> Coverage:
    """Count half-open truth windows on retained times, preserving original bounds."""
    mask.validate_alignment(curve)
    if len(curve.time_days) > 50000:
        raise DataValidationError("coverage samples: exceed 50000; use bounded prepared data")
    windows = _windows(curve, spec)
    time = curve.time_days[mask.keep]
    full, counts, observed, partial, partial_counts = [], [], [], [], []
    total = 0
    for center, left, right, complete in windows:
        n = int(np.searchsorted(time, right, side="left") - np.searchsorted(time, left))
        total += n
        if complete:
            full.append(center)
            counts.append(n)
            if n >= config.minimum_points_per_observed_transit:
                observed.append(center)
        else:
            partial.append(center)
            partial_counts.append(n)
    return Coverage(
        tuple(full),
        tuple(counts),
        tuple(observed),
        tuple(partial),
        tuple(partial_counts),
        total,
        len(observed) >= config.minimum_observed_transits
        and total >= config.minimum_total_in_transit_points,
    )


@dataclass(frozen=True)
class Retention:
    retained_depth: Measurement
    ratio: Measurement


def paired_retention(
    curve: PreparedCurve,
    mask: ObservationMask,
    spec: InjectionSpec,
    background_time_days: ArrayLike,
    z0: ArrayLike | None,
    injected_time_days: ArrayLike,
    z1: ArrayLike | None,
    config: EvaluationConfig = EvaluationConfig(),
) -> Retention:
    """Ordinary means after independent processing; explicitly verify paired times.

    None represents failed/missing processing, never a numeric zero. Misaligned
    or invalid supplied arrays are boundary errors, not scientific misses.
    """
    mask.validate_alignment(curve)
    if len(curve.time_days) > 50000:
        raise DataValidationError("retention samples: exceed 50000; use bounded prepared data")
    expected = curve.time_days[mask.keep]
    for time in (background_time_days, injected_time_days):
        source = _source_array(time, "retention time")
        if source.shape != expected.shape or not np.array_equal(source, expected):
            raise DataValidationError(
                "retention time: misaligned; use identical retained timestamps"
            )
    if z0 is None or z1 is None:
        missing = Measurement(None, "paired_processing_unavailable")
        return Retention(missing, missing)
    if not len(expected):
        missing = Measurement(None, "insufficient_retention_in_points")
        return Retention(missing, missing)
    if any(_source_array(z, "retention flux").shape != expected.shape for z in (z0, z1)):
        raise DataValidationError("retention flux: misaligned; supply one value per retained time")
    arrays = [_vector(z, name) for z, name in ((z0, "z0"), (z1, "z1"))]
    inside = np.zeros(len(expected), dtype=bool)
    for _, left, right, _ in _windows(curve, spec):
        inside[np.searchsorted(expected, left) : np.searchsorted(expected, right)] = True
    reason = None
    if np.count_nonzero(inside) < config.minimum_retention_in_points:
        reason = "insufficient_retention_in_points"
    elif np.count_nonzero(~inside) < config.minimum_retention_out_points:
        reason = "insufficient_retention_out_points"
    if reason:
        missing = Measurement(None, reason)
        return Retention(missing, missing)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        q = arrays[0] - arrays[1]
        depth = float(np.mean(q[inside]) - np.mean(q[~inside]))
        ratio = float(np.divide(depth, spec.depth)) if spec.depth else None
    if not np.all(np.isfinite(q)) or not math.isfinite(depth):
        raise DataValidationError(
            "retention depth: overflow; use finite representable processed flux"
        )
    if ratio is not None and not math.isfinite(ratio):
        raise DataValidationError("retention ratio: overflow; use representable depth")
    return Retention(
        Measurement(depth),
        Measurement(ratio) if ratio is not None else Measurement(None, "zero_injected_depth"),
    )


def _match(
    search: SearchResult, spec: InjectionSpec, support: Coverage, config: EvaluationConfig
) -> tuple[Measurement, Measurement, bool | None]:
    if search.status != "ok":
        missing = Measurement(None, "search_error")
        return missing, missing, None
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        relative = abs(search.period_days / spec.period_days - 1)
        # Reduce the fitted epoch before subtracting, preserving integer-cycle invariance.
        period = search.period_days
        errors = [
            abs(((c % period - search.epoch_days % period + period / 2) % period) - period / 2)
            for c in support.observed_centers_days
        ]
    if not math.isfinite(relative) or any(not math.isfinite(x) for x in errors):
        raise DataValidationError("recovery geometry: overflow; use representable periods/epochs")
    timing = Measurement(max(errors)) if errors else Measurement(None, "no_observed_full_events")
    exact = (
        relative <= config.relative_period_tolerance
        and timing.value is not None
        and timing.value <= spec.duration_days * config.maximum_center_error_duration_fraction
    )
    return Measurement(relative), timing, bool(exact)


def evaluate_injection(
    curve: PreparedCurve,
    mask: ObservationMask,
    spec: InjectionSpec,
    search: SearchResult,
    background_search: SearchResult | None,
    retention: Retention | None = None,
    config: EvaluationConfig = EvaluationConfig(),
    search_config: SearchConfig = SearchConfig(),
) -> EvaluationResult:
    """Score already completed same-configuration searches; execute no processing.

    The caller must supply the reused background for this star/mask/pipeline.
    T06 is responsible for validating those row/cache identities in orchestration.
    """
    support = coverage(curve, mask, spec, config)
    relative, timing, geometric = _match(search, spec, support, config)
    retention = retention or Retention(
        Measurement(None, "paired_processing_unavailable"),
        Measurement(None, "paired_processing_unavailable"),
    )
    alias_values = (spec.period_days / 2, spec.period_days * 2)
    if any(not math.isfinite(p) or p <= 0 for p in alias_values):
        raise DataValidationError("alias period: unrepresentable; use bounded periods")
    searchable = tuple(
        search_config.minimum_period_days <= p <= search_config.maximum_period_days
        for p in alias_values
    )
    aliases = (None, None)
    if search.status == "error":
        status, exact, reason = "error", None, "search_error"
    elif not support.eligible:
        status, exact, reason = "insufficient_coverage", None, "insufficient_coverage"
    else:
        exact, reason = geometric, None
        aliases = tuple(
            bool(not exact and abs(search.period_days / p - 1) <= config.relative_period_tolerance)
            for p in alias_values
        )
        status = (
            "exact"
            if exact
            else "half_period_peak"
            if aliases[0]
            else "double_period_peak"
            if aliases[1]
            else "other_peak"
        )
    background, incremental = None, None
    if not support.eligible:
        background_reason = "insufficient_coverage"
    elif background_search is None:
        background_reason = "background_search_unavailable"
    elif background_search.status != "ok":
        background_reason = "background_search_error"
    else:
        if background_search.grid_hash != search.grid_hash:
            raise DataValidationError("background grid: mismatch; reuse the same independent grid")
        background = _match(background_search, spec, support, config)[2]
        background_reason = None
        incremental = bool(exact and not background) if exact is not None else None
    return EvaluationResult(
        status,
        len(support.observed_centers_days),
        support.total_in_transit_points,
        len(support.partial_centers_days),
        exact,
        relative,
        timing,
        *aliases,
        *searchable,
        retention.ratio,
        retention.retained_depth,
        background,
        incremental,
        reason,
        len(support.full_centers_days),
        support.eligible,
        background_reason,
        support.full_centers_days,
        support.full_event_points,
        support.observed_centers_days,
        support.partial_centers_days,
        support.partial_event_points,
    )


def evaluate_control(search: SearchResult) -> EvaluationResult:
    """Both control kinds have no injection truth or recovery/retention label."""
    missing = Measurement(None, "no_injection_truth")
    return EvaluationResult(
        "control" if search.status == "ok" else "error",
        None,
        None,
        None,
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
        "no_injection_truth" if search.status == "ok" else "search_error",
        background_match_reason="no_injection_truth",
    )


def summarize_injections(
    rows: Mapping[tuple[str, str], EvaluationResult],
    planned_rows: tuple[tuple[str, str], ...],
) -> dict[str, dict[str, int]]:
    """Descriptive per-configuration counts; never discard failed/ineligible rows."""
    if not planned_rows or len(planned_rows) > 1000 or len(set(planned_rows)) != len(planned_rows):
        raise DataValidationError("planned rows: empty/duplicate/oversized; supply unique row IDs")
    if set(rows) != set(planned_rows):
        raise DataValidationError("summary rows: missing/extra IDs; preserve every planned row")
    summaries = {}
    for _, pipeline in planned_rows:
        summaries.setdefault(
            pipeline,
            dict.fromkeys(
                (
                    "planned",
                    "coverage_eligible",
                    "exact",
                    "insufficient_coverage",
                    "technical_failures",
                    "exact_with_background_match",
                    "exact_without_background_match",
                    "exact_background_unknown",
                ),
                0,
            ),
        )
    for key in planned_rows:
        result, counts = rows[key], summaries[key[1]]
        if result.coverage_eligible is None:
            raise DataValidationError("summary coverage: missing truth; supply injection results")
        counts["planned"] += 1
        counts["coverage_eligible"] += int(result.coverage_eligible)
        counts["exact"] += int(result.exact_recovery is True)
        counts["insufficient_coverage"] += int(not result.coverage_eligible)
        counts["technical_failures"] += int(result.status == "error")
        if result.exact_recovery:
            field = (
                "exact_background_unknown"
                if result.background_already_matches is None
                else "exact_with_background_match"
                if result.background_already_matches
                else "exact_without_background_match"
            )
            counts[field] += 1
    return summaries
