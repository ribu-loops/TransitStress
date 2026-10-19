"""Shared normalization and occupied 10-minute bins; no interpolation or masking."""

from dataclasses import replace

import numpy as np
from numpy.typing import ArrayLike

from transitstress.config import PreparationConfig
from transitstress.errors import DataValidationError
from transitstress.models import PreparedCurve, TimeMetadata


def prepare_curve(
    time_days: ArrayLike,
    flux: ArrayLike,
    flux_err: ArrayLike,
    *,
    curve_id: str,
    source_sha256: str,
    time_metadata: TimeMetadata,
    config: PreparationConfig = PreparationConfig(),
) -> PreparedCurve:
    """Sort validated day-valued rows, shift to first row and normalize once.

    Archive filtering and strict CSV validation occur at their separate boundaries.
    The supplied epoch must already describe the first retained row (when known).
    Source coordinate bounds belong in the caller's provenance, including unknown
    CSV epochs; PreparedCurve preserves the relative *unbinned* bounds.
    """
    try:
        arrays = [np.array(a, dtype=np.float64, copy=True) for a in (time_days, flux, flux_err)]
    except (ValueError, TypeError, OverflowError) as error:
        raise DataValidationError("rows: invalid numbers; supply finite numeric vectors") from error
    t, f, e = arrays
    if any(a.ndim != 1 or a.size < 2 for a in arrays) or not (t.shape == f.shape == e.shape):
        raise DataValidationError("rows: invalid lengths; supply at least two aligned rows")
    if any(not np.all(np.isfinite(a)) for a in arrays) or np.any(f <= 0) or np.any(e <= 0):
        raise DataValidationError(
            "rows: invalid values; supply finite times and positive flux/errors"
        )
    order = np.argsort(t, kind="stable")
    t, f, e = (a[order] for a in arrays)
    if np.any(t[1:] == t[:-1]):
        raise DataValidationError("time_days: duplicate timestamps; supply distinct observations")
    with np.errstate(all="ignore"):
        t = t - t[0]
        median = float(np.median(f))
        f, e = f / median, e / median
        q = t / (config.bin_minutes / 1440)
    if (
        not np.isfinite(median)
        or median <= 0
        or any(not np.all(np.isfinite(a)) for a in (t, f, e, q))
        or np.any(f <= 0)
        or np.any(e <= 0)
        or np.any(q >= 2**53)
    ):
        raise DataValidationError(
            "normalization: unrepresentable range; supply representable values"
        )
    nearest = np.rint(q)
    q = np.where(np.abs(q - nearest) <= config.bin_boundary_snap_tolerance, nearest, q)
    indices = np.floor(q).astype(np.int64)
    _, inverse, counts = np.unique(indices, return_inverse=True, return_counts=True)
    with np.errstate(all="ignore"):
        weights = np.square(1 / e)
        sums = np.bincount(inverse, weights=weights)
        # Normalize weights before multiplying flux/time to avoid avoidable overflow.
        fractions = weights / sums[inverse]
        bt = np.bincount(inverse, weights=fractions * t)
        bf = np.bincount(inverse, weights=fractions * f)
        be = np.sqrt(1 / sums)
    if (
        any(not np.all(np.isfinite(a)) for a in (weights, sums, bt, bf, be))
        or np.any(weights <= 0)
        or np.any(sums <= 0)
        or np.any(be <= 0)
    ):
        raise DataValidationError(
            "flux_err weights: overflow/underflow; supply representable errors"
        )
    return PreparedCurve(
        curve_id=curve_id,
        time_days=bt,
        flux=bf,
        flux_err=be,
        original_start_days=0.0,
        original_end_days=float(t[-1]),
        bin_anchor_days=0.0,
        support_counts=counts,
        time_metadata=replace(time_metadata, normalization_factor=median),
        source_sha256=source_sha256,
    )
