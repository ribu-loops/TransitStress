"""Blind, segmented time-window medians using the eligibility window layout."""

from bisect import bisect_left, insort
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from transitstress.errors import DataValidationError
from transitstress.models import DetrendConfig, _vector
from transitstress.support import window_layout


@dataclass(frozen=True)
class DetrendResult:
    flux: NDArray[np.float64]
    flux_err: NDArray[np.float64]
    trend: NDArray[np.float64]
    neighbor_counts: NDArray[np.int64]
    segment_ids: NDArray[np.int64]


def detrend(
    time_days: ArrayLike, flux: ArrayLike, flux_err: ArrayLike, config: DetrendConfig
) -> DetrendResult:
    """Divide flux/errors by a freshly fitted trend, without clipping or renormalizing.

    Sorted insertion/removal avoids sorting every overlapping window. Layout is
    exactly the shared inclusive, segmented T03 layout; the center counts.
    Trend uncertainty and induced correlation are not propagated into errors.
    Negative additive injected flux is allowed; the median trend must be positive.
    """
    time, values, errors = (
        _vector(time_days, "time_days"),
        _vector(flux, "flux"),
        _vector(flux_err, "flux_err", positive=True),
    )
    if not len(time) == len(values) == len(errors) or len(time) > 50000:
        raise DataValidationError("detrend lengths: align <=50000 time/flux/error samples")
    layout = window_layout(time, config.window_days, config.segment_gap_days)
    counts = layout.neighbor_counts
    if np.any(counts < config.minimum_neighbors):
        raise DataValidationError("detrend support: fewer than five neighbors; preserve failure")
    ordered: list[float] = []
    previous_left = previous_right = 0
    trend = np.empty(len(time), dtype=float)
    for i, (left, right) in enumerate(zip(layout.left, layout.right, strict=True)):
        if left >= previous_right:
            ordered = sorted(values[left:right].tolist())
        else:
            for j in range(previous_left, left):
                ordered.pop(bisect_left(ordered, float(values[j])))
            for j in range(previous_right, right):
                insort(ordered, float(values[j]))
        middle = len(ordered) // 2
        trend[i] = (
            ordered[middle] if len(ordered) % 2 else ordered[middle - 1] / 2 + ordered[middle] / 2
        )
        previous_left, previous_right = left, right
    if not np.all(np.isfinite(trend) & (trend > 0)):
        raise DataValidationError("detrend trend: nonpositive/nonfinite; preserve invalid input")
    with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
        z, error_z = values / trend, errors / trend
        weights = np.square(1 / error_z)
    if not (
        np.all(np.isfinite(z))
        and np.all(np.isfinite(error_z) & (error_z > 0))
        and np.all(np.isfinite(weights) & (weights > 0))
        and np.isfinite(np.sum(weights))
    ):
        raise DataValidationError("detrend division/weights: overflow/underflow; use valid units")
    for array in (z, error_z, trend):
        array.setflags(write=False)
    return DetrendResult(z, error_z, trend, counts, layout.segment_ids)
