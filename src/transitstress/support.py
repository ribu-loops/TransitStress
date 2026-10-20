"""Time-only inclusive rolling windows and gap segmentation, shared with T04."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from transitstress.errors import DataValidationError
from transitstress.models import _number, _vector


@dataclass(frozen=True)
class WindowLayout:
    """Slices [left[i]:right[i]] never cross a segment; endpoints are inclusive."""

    left: NDArray[np.int64]
    right: NDArray[np.int64]
    segment_ids: NDArray[np.int64]

    @property
    def neighbor_counts(self) -> NDArray[np.int64]:
        counts = self.right - self.left
        counts.setflags(write=False)
        return counts


def window_layout(
    time_days: ArrayLike, window_days: float, segment_gap_days: float
) -> WindowLayout:
    """O(N) windows; count the center itself, split only on gaps strictly > limit.

    Compare differences directly (also in the independent direct-window tests),
    avoiding rounded center +/- half-window boundaries and any tolerance widening.
    """
    time = _vector(time_days, "time_days")
    _number(window_days, "window_days", positive=True)
    _number(segment_gap_days, "segment_gap_days", positive=True)
    with np.errstate(over="ignore", invalid="ignore"):
        differences = np.diff(time)
        span = time[-1] - time[0]
    if not np.isfinite(span) or not np.all(np.isfinite(differences) & (differences > 0)):
        raise DataValidationError("time_days: unordered/overflowing; use increasing finite days")
    half = window_days / 2
    if half == 0:
        raise DataValidationError("window_days: underflow; use a representable half-window")
    boundaries = np.r_[0, np.flatnonzero(differences > segment_gap_days) + 1, len(time)]
    left = np.empty(len(time), dtype=np.int64)
    right = np.empty_like(left)
    segments = np.empty_like(left)
    for segment, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
        lo = hi = start
        for i in range(start, end):
            while time[i] - time[lo] > half:
                lo += 1
            while hi < end and time[hi] - time[i] <= half:
                hi += 1
            left[i], right[i], segments[i] = lo, hi, segment
    for array in (left, right, segments):
        array.setflags(write=False)
    return WindowLayout(left, right, segments)


def window_support(
    time_days: ArrayLike, window_days: float, segment_gap_days: float
) -> NDArray[np.int64]:
    """Return one neighbor count per timestamp; no flux or truth accepted."""
    return window_layout(time_days, window_days, segment_gap_days).neighbor_counts
