"""Explicit Gaussian inputs and complete case enumeration; no search execution."""

from dataclasses import dataclass, replace

import numpy as np

from transitstress.config import ProtocolConfig
from transitstress.eligibility import enumerate_cases
from transitstress.errors import DataValidationError
from transitstress.masking import make_observation_mask
from transitstress.models import PreparedCurve
from transitstress.provenance import canonical_hash, derive_seed


def gaussian_settings(curve: PreparedCurve, master_seed: int, replicate: int) -> tuple[int, float]:
    """Seed excludes mask/pipeline: sample the original basis once, then mask."""
    if type(replicate) is not int or replicate not in (0, 1, 2):
        raise DataValidationError("Gaussian replicate: unsupported; use 0, 1 or 2")
    if len(curve.time_days) > 50000:
        raise DataValidationError("Gaussian samples: exceed 50000; supply bounded prepared data")
    seed = derive_seed(master_seed, curve.curve_id, "gaussian_null", replicate)
    # Dividing before addition avoids overflow in the even-length median average.
    ordered = np.sort(curve.flux_err)
    n = len(ordered)
    sigma = (
        float(ordered[n // 2]) if n % 2 else float(ordered[n // 2 - 1] / 2 + ordered[n // 2] / 2)
    )
    if not np.isfinite(sigma) or sigma <= 0:
        raise DataValidationError("Gaussian sigma: invalid; use finite positive prepared errors")
    return seed, sigma


@dataclass(frozen=True)
class GaussianControl:
    curve: PreparedCurve
    parent_prepared_hash: str
    seed: int
    sigma: float
    replicate: int


def gaussian_control(curve: PreparedCurve, master_seed: int, replicate: int) -> GaussianControl:
    """Full original timestamps, unity + PCG64 noise, constant sigma errors.

    The wrapper distinguishes generated data from its real parent. Metadata,
    original bounds and index support are inherited; this is never archive data.
    """
    seed, sigma = gaussian_settings(curve, master_seed, replicate)
    rng = np.random.Generator(np.random.PCG64(seed))
    with np.errstate(over="ignore", invalid="ignore"):
        flux = 1.0 + rng.normal(0.0, sigma, len(curve.time_days))
    if not np.all(np.isfinite(flux) & (flux > 0)):
        raise DataValidationError(
            "Gaussian flux: nonpositive/nonfinite realization; preserve failure without resampling"
        )
    generated = replace(curve, flux=flux, flux_err=np.full(len(flux), sigma))
    return GaussianControl(generated, curve.content_hash, seed, sigma, replicate)


def enumerate_experiment(
    manifest: dict, curves: dict[str, PreparedCurve], config: ProtocolConfig, split: str
) -> tuple[dict, ...]:
    """Keep existing injection IDs; add one real and three nulls per parent/mask.

    This only enumerates metadata. Gaussian vectors and searches are not run.
    The same case ID serves both configurations; mask definition stays in the
    identity even if a natural gap makes two keep arrays identical.
    """
    injections = enumerate_cases(manifest, curves, config, split)
    cases = [dict(c, seed=None, sigma=None) for c in injections]
    for product in manifest["products"]:
        if product["role"] != split:
            continue
        curve = curves[product["tic_id"]]
        settings = [
            gaussian_settings(curve, config.master_seed, replicate)
            for replicate in range(config.controls.gaussian_replicates_per_star_mask)
        ]
        for name in config.masks.names:
            mask = make_observation_mask(curve, name, config.master_seed, config.masks)
            common = dict(
                curve_id=curve.curve_id,
                prepared_hash=curve.content_hash,
                mask_definition=name,
                mask_hash=mask.checksum,
            )
            identity = dict(common, kind="uninjected_real")
            cases.append(
                dict(
                    identity,
                    case_id=canonical_hash(identity),
                    injection=None,
                    seed=None,
                    sigma=None,
                )
            )
            for replicate, (seed, sigma) in enumerate(settings):
                identity = dict(
                    common, kind="gaussian_null", replicate=replicate, seed=seed, sigma=sigma
                )
                cases.append(dict(identity, case_id=canonical_hash(identity), injection=None))
    expected = 78 if split == "development" else 195
    if len(cases) != expected or len({c["case_id"] for c in cases}) != expected:
        raise DataValidationError("experiment cases: wrong counts/IDs; restore final split")
    if len(cases) * 2 > config.limits.maximum_result_rows:
        raise DataValidationError("result rows: exceed budget; revise scope before execution")
    return tuple(cases)


def planned_result_rows(cases: tuple[dict, ...]) -> tuple[dict, ...]:
    """JSON-ready planned rows, preserving case identity; not a T06 result writer."""
    if not cases or len(cases) * 2 > 1000:
        raise DataValidationError("planned rows: empty/oversized; supply 1–500 cases")
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise DataValidationError(
            "case_id: duplicated; retain one case shared by both configurations"
        )
    return tuple(
        dict(c, pipeline_id=pipeline) for c in cases for pipeline in ("rm_0p5d", "rm_1p5d")
    )
