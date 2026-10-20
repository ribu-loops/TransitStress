"""Independent original-baseline grids and blind Astropy BLS diagnostics."""

import math
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from astropy.timeseries import BoxLeastSquares
from numpy.typing import ArrayLike, NDArray

from transitstress.config import SearchConfig
from transitstress.errors import DataValidationError
from transitstress.models import PeriodGrid, PreparedCurve, SearchResult, _number, _vector
from transitstress.provenance import canonical_hash


def _grid_spacing(baseline: float, config: SearchConfig) -> tuple[float, int]:
    _number(baseline, "original_baseline_days", positive=True)
    # Match locked Astropy autoperiod's documented frequency spacing. Predict
    # before invoking its allocator, including its rounded endpoint sample.
    try:
        df = config.frequency_factor * min(config.duration_days) / baseline**2
        intervals = (1 / config.minimum_period_days - 1 / config.maximum_period_days) / df
        if not math.isfinite(intervals) or 1 + math.ceil(intervals) > 50000:
            raise ValueError("over budget")
    except (OverflowError, ZeroDivisionError, ValueError) as error:
        raise DataValidationError(
            "period grid: nonfinite/over 50000 predicted samples; shorten original baseline"
        ) from error
    return df, 1 + int(np.round(intervals))


def build_search_grid(original_curve: PreparedCurve, config: SearchConfig) -> PeriodGrid:
    """Use preserved original bounds, never masked endpoints or an expected period.

    Astropy may round the last sample just above 10 d. Retain only samples in
    the fixed inclusive bounds; do not insert or move any sample to an answer.
    """
    baseline = original_curve.original_end_days - original_curve.original_start_days
    _, predicted = _grid_spacing(baseline, config)
    model = BoxLeastSquares([0.0, baseline], [1.0, 1.0])
    periods = model.autoperiod(
        config.duration_days,
        minimum_period=config.minimum_period_days,
        maximum_period=config.maximum_period_days,
        frequency_factor=config.frequency_factor,
    )
    if len(periods) != predicted or len(periods) > 50000:
        raise DataValidationError("period grid: allocator size mismatch; inspect locked Astropy")
    periods = periods[
        (periods >= config.minimum_period_days) & (periods <= config.maximum_period_days)
    ]
    return PeriodGrid(periods, baseline, canonical_hash(config))


def validate_search_grid(grid: PeriodGrid, config: SearchConfig) -> None:
    """Refuse narrowed/inserted grids even on direct API calls, without autoperiod."""
    df, count = _grid_spacing(grid.original_baseline_days, config)
    expected = 1 / (1 / config.minimum_period_days - df * np.arange(count))
    expected = expected[expected <= config.maximum_period_days]
    if grid.search_config_hash != canonical_hash(config) or not np.array_equal(
        grid.periods_days, expected
    ):
        raise DataValidationError(
            "period grid: not the full independent grid; rebuild from original"
        )


@dataclass(frozen=True)
class BLSOutput:
    result: SearchResult
    periodogram: dict[str, NDArray[np.float64]]


def search_bls(
    time_days: ArrayLike,
    detrended_flux: ArrayLike,
    detrended_error: ArrayLike,
    period_grid: PeriodGrid,
    config: SearchConfig,
) -> BLSOutput:
    """Search all periods and durations; largest finite likelihood wins.

    Increasing periods implement the lower-period tie rule. Astropy returns one
    duration/epoch per period, retained unchanged. No significance is inferred.
    """
    started = perf_counter()
    validate_search_grid(period_grid, config)
    time = _vector(time_days, "time_days")
    flux = _vector(detrended_flux, "detrended_flux")
    error = _vector(detrended_error, "detrended_error", positive=True)
    if not (5 <= len(time) <= 50000 and len(time) == len(flux) == len(error)):
        raise DataValidationError("BLS lengths: align 5–50000 time/flux/error samples")
    with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
        # Match the native BLS variance arithmetic, including its overflow limits.
        variance = np.square(error)
        weights = 1 / variance
        centered = flux - np.median(flux)
        weighted_square = np.square(centered / error)
        total_weight = np.sum(weights)
        total_square = np.sum(weighted_square)
        span = time[-1] - time[0]
    if not (
        5 <= len(time) <= 50000
        and len(time) == len(flux) == len(error)
        and np.all(np.diff(time) > 0)
        and 0 < span <= period_grid.original_baseline_days
        and np.all(np.isfinite(weights) & (weights > 0))
        and np.all(np.isfinite(variance) & (variance > 0))
        and np.isfinite(total_weight)
        and np.isfinite(total_square)
    ):
        raise DataValidationError(
            "BLS input: invalid lengths/times/weights or numerical overflow; check units/errors"
        )
    model = BoxLeastSquares(time, flux, dy=error)
    raw = model.power(
        period_grid.periods_days,
        config.duration_days,
        objective=config.objective,
        method=config.method,
        oversample=config.oversample,
    )
    names = {
        "period_days": "period",
        "epoch_days": "transit_time",
        "duration_days": "duration",
        "depth": "depth",
        "depth_err": "depth_err",
        "depth_snr": "depth_snr",
        "power": "power",
    }
    arrays = {name: np.array(raw[source], dtype=float, copy=True) for name, source in names.items()}
    for array in arrays.values():
        array.setflags(write=False)
    finite = np.flatnonzero(np.isfinite(arrays["power"]))
    reason = None
    if not len(finite):
        reason = "no_finite_BLS_power"
    else:
        peak = int(finite[np.argmax(arrays["power"][finite])])
        if not all(np.isfinite(array[peak]) for array in arrays.values()):
            reason = "nonfinite_strongest_peak_diagnostics"
        elif arrays["depth_err"][peak] <= 0:
            reason = "nonpositive_strongest_peak_depth_error"
    measurements = {name: None if reason else float(array[peak]) for name, array in arrays.items()}
    result = SearchResult(
        status="error" if reason else "ok",
        **measurements,
        grid_hash=period_grid.checksum,
        elapsed_seconds=perf_counter() - started,
        failure_reason=reason,
    )
    return BLSOutput(result, arrays)
