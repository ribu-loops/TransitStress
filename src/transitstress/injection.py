"""Additive box injection and paired inputs; never used by blind window support."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from transitstress.config import InjectionConfig
from transitstress.errors import DataValidationError
from transitstress.models import (
    InjectionSpec,
    Measurement,
    ObservationMask,
    PreparedCurve,
    _number,
    _vector,
)


def box_mask(time_days: ArrayLike, spec: InjectionSpec) -> NDArray[np.bool_]:
    """Half-open [-D/2,D/2) periodic truth, computed on the original time basis."""
    time = _vector(time_days, "time_days")
    # Equivalent cyclic interval edges avoid rounding observations one ULP from
    # an edge onto it when adding P/2. No tolerance widens either boundary.
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        cycle = np.remainder(time, spec.period_days)
        epoch = spec.epoch_days % spec.period_days
        left = (epoch - spec.duration_days / 2) % spec.period_days
        right = (epoch + spec.duration_days / 2) % spec.period_days
    if (
        not np.all(np.isfinite(cycle))
        or not np.all(np.isfinite([left, right]))
        or spec.duration_days / 2 == 0
        or left == right
    ):
        raise DataValidationError("injection phase: overflow/underflow; use representable days")
    if left < right:
        result = (cycle >= left) & (cycle < right)
    else:
        result = (cycle >= left) | (cycle < right)
    result.setflags(write=False)
    return result


def inject_box(curve: PreparedCurve, spec: InjectionSpec) -> NDArray[np.float64]:
    """Return a new flux array; preserve background, errors and every downward dip."""
    truth = box_mask(curve.time_days, spec)
    with np.errstate(over="ignore", invalid="ignore"):
        flux = curve.flux - spec.depth * truth
    if not np.all(np.isfinite(flux)):
        raise DataValidationError("injected flux: overflow; use a representable additive depth")
    flux.setflags(write=False)
    return flux


def family_specs(
    curve: PreparedCurve, config: InjectionConfig = InjectionConfig()
) -> tuple[InjectionSpec, ...]:
    """Nine specifications per star; enumeration does not inject or search."""
    specs = tuple(
        InjectionSpec(
            f.id,
            f.period_days,
            config.duration_hours / 24,
            curve.original_start_days + f.epoch_fraction * f.period_days,
            ppm * 1e-6,
        )
        for f in config.families
        for ppm in config.depths_ppm
    )
    for spec in specs:
        spec.validate_trial()
    return specs


@dataclass(frozen=True)
class PairedInputs:
    """Aligned arrays; pass only explicit time/flux/error arrays to blind processing."""

    time_days: NDArray[np.float64]
    background_flux: NDArray[np.float64]
    injected_flux: NDArray[np.float64]
    flux_err: NDArray[np.float64]


def paired_inputs(curve: PreparedCurve, mask: ObservationMask, spec: InjectionSpec) -> PairedInputs:
    mask.validate_alignment(curve)
    injected = inject_box(curve, spec)
    arrays = [a[mask.keep] for a in (curve.time_days, curve.flux, injected, curve.flux_err)]
    if not arrays[0].size:
        raise DataValidationError("keep: empty observations; choose a supported observation mask")
    for array in arrays:
        array.setflags(write=False)
    return PairedInputs(*arrays)


def retention_ratio(retained_depth: float, injected_depth: float) -> Measurement:
    """Post-processing diagnostic division only; full paired measurement belongs to T05."""
    _number(retained_depth, "retained_depth")
    _number(injected_depth, "injected_depth")
    if injected_depth < 0:
        raise DataValidationError("injected_depth: negative; use a nonnegative depth")
    if injected_depth == 0:
        return Measurement(None, "zero_injected_depth")
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        ratio = float(np.divide(retained_depth, injected_depth))
    return Measurement(ratio)
