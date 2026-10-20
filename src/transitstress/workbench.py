"""Headless exploratory single injection, two configurations, built-in or CSV."""

import json
import math
from pathlib import Path

from transitstress import acquisition as acq
from transitstress.artifacts import json_write, numeric_write
from transitstress.config import ProtocolConfig
from transitstress.csv_input import parse_csv, read_local_csv
from transitstress.errors import DataValidationError
from transitstress.experiment import CaseEngine, load_split, run_cases
from transitstress.identity import execution_provenance, run_identity
from transitstress.masking import make_observation_mask
from transitstress.models import InjectionSpec, PreparedCurve
from transitstress.provenance import canonical_hash, canonical_json


def local_spec(
    curve: PreparedCurve,
    *,
    period_days: float,
    depth_ppm: float,
    duration_hours: float,
    epoch_fraction: float,
) -> InjectionSpec:
    fields = dict(
        period_days=(period_days, 1, 8),
        depth_ppm=(depth_ppm, 100, 5000),
        duration_hours=(duration_hours, 1, 6),
        epoch_fraction=(epoch_fraction, 0, 1),
    )
    for name, (value, low, high) in fields.items():
        if (
            type(value) not in (float, int)
            or not math.isfinite(value)
            or not low <= value <= high
            or (name == "epoch_fraction" and value == 1)
        ):
            raise DataValidationError(f"{name}: outside local bounds; use {low}–{high}")
    return InjectionSpec(
        "exploratory",
        period_days,
        duration_hours / 24,
        curve.original_start_days + epoch_fraction * period_days,
        depth_ppm * 1e-6,
    )


def single_case(
    config: ProtocolConfig,
    *,
    repository: Path,
    output: Path | None = None,
    period_days: float,
    depth_ppm: float,
    duration_hours: float,
    epoch_fraction: float,
    mask_name: str = "original",
    built_in: str | None = None,
    csv_content: bytes | None = None,
    csv_path: str | Path | None = None,
    confirm_days_and_linear_units: bool = False,
    manifest_path: Path = Path("data/manifest.json"),
    cache: Path = Path(".cache/transitstress"),
) -> dict:
    """CSV bytes stay in this call's memory; explicit CLI exports use generated names.

    This API has no dashboard mode dependency. A future controller must enforce
    demo restrictions before calling it. Built-ins are development parents only.
    """
    if sum(value is not None for value in (built_in, csv_content, csv_path)) != 1:
        raise DataValidationError("case input: ambiguous/missing; choose one built-in or CSV")
    notices = ()
    if built_in is not None:
        _, curves = load_split(config, manifest_path, cache, "development")
        if built_in not in curves:
            raise DataValidationError("built-in: unknown development TIC; use 8195886 or 8196058")
        curve = curves[built_in]
    else:
        if csv_content is not None:
            parsed = parse_csv(
                csv_content,
                confirm_days_and_linear_units=confirm_days_and_linear_units,
                config=config,
            )
        else:
            parsed = read_local_csv(
                csv_path, confirm_days_and_linear_units=confirm_days_and_linear_units, config=config
            )
        curve, notices = parsed.curve, parsed.notices
    spec = local_spec(
        curve,
        period_days=period_days,
        depth_ppm=depth_ppm,
        duration_hours=duration_hours,
        epoch_fraction=epoch_fraction,
    )
    mask = make_observation_mask(curve, mask_name, config.master_seed, config.masks)
    common = dict(
        curve_id=curve.curve_id,
        prepared_hash=curve.content_hash,
        mask_definition=mask_name,
        mask_hash=mask.checksum,
    )
    cases = []
    for kind, injection in (("uninjected_real", None), ("injection", spec)):
        identity = dict(common, kind=kind)
        if injection is not None:
            identity["injection"] = injection
        cases.append(
            dict(
                identity,
                case_id=canonical_hash(identity),
                injection=injection,
                seed=None,
                sigma=None,
            )
        )
    identity = run_identity(
        repository,
        settings=dict(protocol=config, spec=spec, mask=mask.checksum),
        data=dict(prepared_hash=curve.content_hash, units=curve.time_metadata),
        split="exploratory",
    )
    identity = json.loads(canonical_json(identity))
    engine = CaseEngine(config, {"input": curve}, canonical_hash(identity))
    if output is None:
        pairs = [engine.compute(case) for case in cases]
        return dict(
            schema_version=1,
            exploratory=True,
            run_id=engine.run_id,
            identity=identity,
            provenance=execution_provenance(repository),
            status="complete",
            actual_rows=4,
            expected_rows=4,
            rows=[row for rows, _ in pairs for row in rows],
            details={
                case["case_id"]: arrays for case, (_, arrays) in zip(cases, pairs, strict=True)
            },
            notices=notices,
        )
    acq.safe_file(output, "run.json")
    if output.exists() and any(output.iterdir()):
        raise DataValidationError("case output: not empty; choose a new directory")
    # Only explicit export calls persist private numeric inputs; no shared/global cache.
    prepared = numeric_write(
        output / "inputs",
        "prepared.npz",
        dict(
            time_days=curve.time_days,
            flux=curve.flux,
            flux_err=curve.flux_err,
            support_counts=curve.support_counts,
        ),
    )
    grid = numeric_write(
        output / "grids", "periods.npz", {"period_days": engine.grids[curve.curve_id].periods_days}
    )
    kept = numeric_write(output / "masks", "keep.npz", {"keep": mask.keep})
    json_write(
        output,
        "numeric_inputs.json",
        dict(
            files={
                "inputs/prepared.npz": prepared,
                "grids/periods.npz": grid,
                "masks/keep.npz": kept,
            }
        ),
    )
    acq.write_json(
        output,
        "settings.json",
        dict(
            protocol=config,
            injection=spec,
            search=config.search,
            exploratory=True,
            units=curve.time_metadata,
            notices=notices,
        ),
    )
    result = run_cases(
        root=output,
        identity=identity,
        cases=tuple(cases),
        engine=engine,
        provenance=execution_provenance(repository),
    )
    from transitstress.reporting import analyze, export_case_zip

    analyze(output)
    export_case_zip(output)
    return result
