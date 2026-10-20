"""Stable masks of original prepared observations, independent of injection truth."""

import numpy as np

from transitstress.config import MaskConfig
from transitstress.errors import DataValidationError
from transitstress.models import ObservationMask, PreparedCurve
from transitstress.provenance import derive_seed, numeric_hash


def make_observation_mask(
    curve: PreparedCurve, definition: str, master_seed: int, config: MaskConfig = MaskConfig()
) -> ObservationMask:
    """Sample once per parent curve; never include family/depth/pipeline in the seed."""
    if definition not in config.names:
        raise DataValidationError("mask definition: unsupported; use a planned observation mask")
    seed = None
    keep = np.ones(curve.time_days.size, dtype=bool)
    if definition == "random_missing":
        seed = derive_seed(master_seed, curve.curve_id, "random_missing")
        rng = np.random.Generator(np.random.PCG64(seed))
        remove = rng.choice(
            len(keep), size=int(np.floor(config.random_removed_fraction * len(keep))), replace=False
        )
        keep[remove] = False
    elif definition == "central_block":
        start, end = block_interval(curve, config)
        keep = (curve.time_days < start) | (curve.time_days >= end)
    return ObservationMask(keep, definition, seed)


def block_interval(curve: PreparedCurve, config: MaskConfig = MaskConfig()) -> tuple[float, float]:
    """Elapsed interval includes natural gaps and preserves the pre-mask bounds."""
    start = curve.original_start_days
    baseline = curve.original_end_days - start
    return (
        start + config.block_start_fraction * baseline,
        start + config.block_end_fraction * baseline,
    )


def mask_record(curve: PreparedCurve, mask: ObservationMask) -> dict:
    """Persist original keep indices as finite JSON plus both canonical checksums."""
    mask.validate_alignment(curve)
    indices = np.flatnonzero(mask.keep)
    return {
        "definition": mask.definition,
        "seed": mask.seed,
        "parent_curve_id": curve.curve_id,
        "prepared_hash": curve.content_hash,
        "keep_indices": indices.tolist(),
        "keep_indices_hash": numeric_hash({"keep_indices": indices}),
        "mask_hash": mask.checksum,
        "input_count": len(mask.keep),
        "retained_count": len(indices),
        "removed_count": mask.removed_count,
        "elapsed_block_days": list(block_interval(curve))
        if mask.definition == "central_block"
        else None,
        "original_bounds_days": [curve.original_start_days, curve.original_end_days],
    }
