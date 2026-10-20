"""Single-worker, fully enumerated paired experiments with durable case checkpoints."""

import contextlib
import csv
import io
import json
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter

import numpy as np
from numpy.typing import NDArray

from transitstress import acquisition as acq
from transitstress.artifacts import (
    json_read,
    json_write,
    numeric_read,
    numeric_write,
    results_csv,
    validate_numeric_inputs,
    validate_plan,
    validate_rows,
    verify_checksum,
)
from transitstress.config import ProtocolConfig, eligibility_reference_hash
from transitstress.controls import (
    enumerate_experiment,
    gaussian_control,
    gaussian_settings,
    planned_result_rows,
)
from transitstress.detrending import DetrendResult, detrend
from transitstress.eligibility import support_evidence
from transitstress.errors import DataValidationError
from transitstress.evaluation import evaluate_control, evaluate_injection, paired_retention
from transitstress.identity import execution_provenance, run_identity
from transitstress.injection import inject_box
from transitstress.masking import make_observation_mask
from transitstress.models import (
    DetrendConfig,
    InjectionSpec,
    ObservationMask,
    PeriodGrid,
    PreparedCurve,
    SearchResult,
)
from transitstress.provenance import canonical_hash, canonical_json, numeric_hash
from transitstress.sanity import PIPELINES
from transitstress.search import build_search_grid, search_bls


@contextlib.contextmanager
def writer_lock(root: Path) -> Iterator[None]:
    """OS lock releases on process death; a stale filename never blocks resume."""
    path = acq.safe_file(root, "writer.lock")
    root.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DataValidationError(
                "run writer: already active; wait for the current job"
            ) from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load_split(config: ProtocolConfig, manifest_path: Path, cache: Path, split: str) -> tuple:
    """Offline exact pinned source verification and shared support checks."""
    manifest = acq.read_json(manifest_path.parent, manifest_path.name)
    acq.require_final_manifest(manifest)
    acq.validate_manifest(manifest, config)
    evidence = acq.read_json(manifest_path.parent, "t03_eligibility.json")
    if canonical_hash(evidence) != manifest["t03_evidence_hash"] or evidence[
        "settings_hash"
    ] != eligibility_reference_hash(config):
        raise DataValidationError("eligibility: changed evidence/settings; restore final pins")
    curves = {}
    for product in manifest["products"]:
        if product["role"] != split:
            continue
        content = acq.cached_source(cache, product)
        if content is None:
            raise DataValidationError("source cache: missing pinned bytes; restore offline")
        curve = acq.verify_pinned(product, content, config, cache)
        saved = next(a for a in evidence["audits"] if a["tic_id"] == product["tic_id"])
        if any(saved.get(k) != v for k, v in support_evidence(curve, config).items()):
            raise DataValidationError("support: changed masks/indices; restore saved evidence")
        curves[product["tic_id"]] = curve
    return manifest, curves


@dataclass
class Processed:
    binding: str
    search: SearchResult
    processed: DetrendResult | None
    arrays: dict[str, NDArray]
    status: str
    reason: str | None
    detrend_seconds: float


def failed_search(grid: PeriodGrid, reason: str) -> SearchResult:
    return SearchResult("error", *([None] * 7), grid.checksum, 0.0, reason)


def process_series(
    time: NDArray,
    flux: NDArray,
    error: NDArray,
    grid: PeriodGrid,
    config: ProtocolConfig,
    pipeline: DetrendConfig,
    binding: str,
) -> Processed:
    started = perf_counter()
    try:
        processed = detrend(time, flux, error, pipeline)
    except DataValidationError as exc:
        return Processed(
            binding,
            failed_search(grid, str(exc)),
            None,
            {},
            "detrend_error",
            str(exc),
            perf_counter() - started,
        )
    elapsed = perf_counter() - started
    arrays = {"flux": processed.flux, "flux_err": processed.flux_err, "trend": processed.trend}
    try:
        output = search_bls(time, processed.flux, processed.flux_err, grid, config.search)
    except DataValidationError as exc:
        return Processed(
            binding,
            failed_search(grid, str(exc)),
            processed,
            arrays,
            "search_error",
            str(exc),
            elapsed,
        )
    # Preserve any nonfinite diagnostic as an explicit validity vector; JSON
    # measurements still use null/reason. The zero slots are encoded, not measured.
    for name, values in output.periodogram.items():
        valid = np.isfinite(values)
        arrays[f"bls_{name}"] = np.where(valid, values, 0.0)
        arrays[f"bls_{name}_valid"] = valid
    status = "ok" if output.result.status == "ok" else "search_error"
    return Processed(
        binding, output.result, processed, arrays, status, output.result.failure_reason, elapsed
    )


class CaseEngine:
    """Bounded in-process cache; no global cache or uploaded-data retention."""

    def __init__(self, config: ProtocolConfig, curves: dict[str, PreparedCurve], run_id: str):
        if not 1 <= len(curves) <= 7 or any(len(c.time_days) > 50000 for c in curves.values()):
            raise DataValidationError("engine inputs: oversized; use 1–7 bounded prepared parents")
        self.config = config
        self.curves = {c.curve_id: c for c in curves.values()}
        self.run_id = run_id
        self.split = "exploratory"
        self.grids = {key: build_search_grid(c, config.search) for key, c in self.curves.items()}
        self.masks = {
            (key, name): make_observation_mask(c, name, config.master_seed, config.masks)
            for key, c in self.curves.items()
            for name in config.masks.names
        }
        self.backgrounds: dict[str, Processed] = {}
        self.gaussians: dict[tuple[str, int], PreparedCurve | DataValidationError] = {}

    def binding(self, curve: PreparedCurve, mask: ObservationMask, pipeline: str) -> str:
        return canonical_hash(
            dict(
                run_id=self.run_id,
                parent=curve.content_hash,
                mask=mask.checksum,
                pipeline=pipeline,
                grid=self.grids[curve.curve_id].checksum,
            )
        )

    def compute(self, case: dict) -> tuple[list[dict], dict[str, NDArray]]:
        curve = self.curves[case["curve_id"]]
        mask = self.masks[curve.curve_id, case["mask_definition"]]
        if case["prepared_hash"] != curve.content_hash or case["mask_hash"] != mask.checksum:
            raise DataValidationError("case binding: changed parent/mask; restore enumerated plan")
        grid, keep = self.grids[curve.curve_id], mask.keep
        time = curve.time_days[keep]
        arrays = dict(
            time_days=time, background_flux=curve.flux[keep], input_flux_err=curve.flux_err[keep]
        )
        spec = case["injection"]
        if type(spec) is dict:
            spec = InjectionSpec(**spec)
        invalid = None
        try:
            if case["kind"] == "injection":
                flux, error = inject_box(curve, spec)[keep], curve.flux_err[keep]
            elif case["kind"] == "gaussian_null":
                if (case.get("seed"), case.get("sigma")) != gaussian_settings(
                    curve, self.config.master_seed, case["replicate"]
                ):
                    raise DataValidationError("Gaussian identity: changed seed/sigma; restore plan")
                key = curve.curve_id, case["replicate"]
                if key not in self.gaussians:
                    try:
                        self.gaussians[key] = gaussian_control(
                            curve, self.config.master_seed, case["replicate"]
                        ).curve
                    except DataValidationError as exc:
                        self.gaussians[key] = exc
                generated = self.gaussians[key]
                if isinstance(generated, DataValidationError):
                    raise generated
                flux, error = generated.flux[keep], generated.flux_err[keep]
            else:
                flux, error = curve.flux[keep], curve.flux_err[keep]
            arrays.update(input_flux=flux, input_flux_err=error)
        except DataValidationError as exc:
            invalid = str(exc)
        rows = []
        for pipeline in PIPELINES:
            name = pipeline.pipeline_id
            binding = self.binding(curve, mask, name)
            background = self.backgrounds.get(binding)
            if case["kind"] == "injection" and background is None:
                raise DataValidationError("background binding: missing; run real controls first")
            if invalid:
                output = Processed(
                    binding, failed_search(grid, invalid), None, {}, "invalid_input", invalid, 0.0
                )
            else:
                output = process_series(time, flux, error, grid, self.config, pipeline, binding)
            if case["kind"] == "uninjected_real":
                self.backgrounds[binding] = output
            if case["kind"] == "injection":
                if background.binding != binding or background.search.grid_hash != grid.checksum:
                    raise DataValidationError(
                        "background binding: mismatch; reject cross-case reuse"
                    )
                retention, retention_error = None, None
                if background.processed is not None and output.processed is not None:
                    try:
                        retention = paired_retention(
                            curve,
                            mask,
                            spec,
                            time,
                            background.processed.flux,
                            time,
                            output.processed.flux,
                            self.config.evaluation,
                        )
                    except DataValidationError as exc:
                        retention_error = str(exc)
                evaluation = evaluate_injection(
                    curve,
                    mask,
                    spec,
                    output.search,
                    background.search,
                    retention,
                    self.config.evaluation,
                    self.config.search,
                )
                if retention_error:
                    output.status, output.reason = "invalid_input", retention_error
                    evaluation = replace(
                        evaluation,
                        status="error",
                        exact_recovery=None,
                        failure_reason="invalid_paired_retention",
                        exact_after_injection_without_background_match=None,
                    )
            else:
                evaluation = evaluate_control(output.search)
            row = dict(
                schema_version=1,
                run_id=self.run_id,
                protocol_id=self.config.protocol_id,
                split=self.split,
                case_id=case["case_id"],
                curve_id=curve.curve_id,
                case_kind=case["kind"],
                pipeline_id=name,
                mask_name=case["mask_definition"],
                mask_hash=mask.checksum,
                parent_prepared_hash=curve.content_hash,
                input_hash=numeric_hash({"time_days": time, "flux": flux, "flux_err": error})
                if not invalid
                else None,
                background_binding=binding,
                period_grid_hash=grid.checksum,
                n_original=len(keep),
                n_observed=int(np.count_nonzero(keep)),
                actual_removed_fraction=float(1 - np.mean(keep)),
                seed=case.get("seed"),
                sigma=case.get("sigma"),
                detrend_window_days=pipeline.window_days,
                status=output.status,
                reason=output.reason,
                detrend_seconds=output.detrend_seconds,
                search_seconds=output.search.elapsed_seconds,
                search=asdict(output.search),
                evaluation=asdict(evaluation),
                scientific_outcome=evaluation.scientific_outcome,
                background_search_reason=background.reason if background else None,
                units=asdict(curve.time_metadata),
                original_bounds_days=[curve.original_start_days, curve.original_end_days],
                **{
                    k: v
                    for k, v in asdict(output.search).items()
                    if k not in ("status", "failure_reason")
                },
                **{
                    k: v
                    for k, v in asdict(evaluation).items()
                    if k not in ("status", "failure_reason")
                },
            )
            for key in ("period_days", "epoch_days", "duration_days", "depth"):
                row[f"true_{key}"] = getattr(spec, key) if spec else None
            row["missing_reasons"] = {
                key: (
                    value["reason"]
                    if type(value) is dict
                    else output.reason or evaluation.failure_reason or "not_applicable"
                )
                for key, value in row.items()
                if value is None or (type(value) is dict and value.get("value", 0) is None)
            }
            if output.status != "ok":
                row["scientific_outcome"] = "not_applicable"
            rows.append(row)
            arrays.update({f"{name}_{k}": v for k, v in output.arrays.items()})
        return rows, arrays

    def restore_background(self, case: dict, rows: list[dict], arrays: dict[str, NDArray]) -> None:
        if case["kind"] != "uninjected_real":
            return
        curve = self.curves[case["curve_id"]]
        mask = self.masks[curve.curve_id, case["mask_definition"]]
        for row in rows:
            name = row["pipeline_id"]
            binding = self.binding(curve, mask, name)
            if row["background_binding"] != binding:
                raise DataValidationError("background cache: identity mismatch; recompute case")
            processed = None
            if f"{name}_trend" in arrays:
                processed = DetrendResult(
                    arrays[f"{name}_flux"],
                    arrays[f"{name}_flux_err"],
                    arrays[f"{name}_trend"],
                    np.array([]),
                    np.array([]),
                )
            self.backgrounds[binding] = Processed(
                binding,
                SearchResult(**row["search"]),
                processed,
                {},
                row["status"],
                row["reason"],
                row["detrend_seconds"],
            )


def _save_case(root: Path, case: dict, rows: list[dict], arrays: dict[str, NDArray]) -> dict:
    cid = case["case_id"]
    artifact_reason = None
    try:
        record = numeric_write(root / "details", f"{cid}.npz", arrays)
    except DataValidationError as error:
        record, artifact_reason = None, str(error)
        for row in rows:
            row.update(
                status="artifact_error",
                reason=artifact_reason,
                scientific_outcome="not_applicable",
                exact_recovery=None,
                exact_after_injection_without_background_match=None,
            )
            row["evaluation"].update(
                status="error",
                failure_reason="artifact_error",
                exact_recovery=None,
                exact_after_injection_without_background_match=None,
            )
    value = dict(
        schema_version=1, case_id=cid, rows=rows, numerical=record, artifact_reason=artifact_reason
    )
    checksum = json_write(root / "cases", f"{cid}.json", value)
    return dict(checksum=checksum, numerical=record, completed_utc=acq.utc_now())


def read_case(root: Path, case: dict, checkpoint: dict, run_id: str) -> tuple:
    cid = case["case_id"]
    if not verify_checksum(root / "cases", f"{cid}.json", checkpoint["checksum"]):
        raise DataValidationError("case checksum: mismatch; recompute preserved corrupt case")
    value = json_read(root / "cases", f"{cid}.json")
    validate_rows(value["rows"], [case])
    if (
        value["case_id"] != cid
        or value["numerical"] != checkpoint["numerical"]
        or any(
            r["run_id"] != run_id
            or r["parent_prepared_hash"] != case["prepared_hash"]
            or r["mask_hash"] != case["mask_hash"]
            for r in value["rows"]
        )
    ):
        raise DataValidationError("case metadata: identity mismatch; reject mixed outputs")
    if value["numerical"] is None:
        if not value.get("artifact_reason") or any(
            r["status"] != "artifact_error" for r in value["rows"]
        ):
            raise DataValidationError("case artifact failure: missing reason; preserve failed rows")
        arrays = {}
    else:
        arrays = numeric_read(root / "details", f"{cid}.npz", value["numerical"])
    return value["rows"], arrays


def _preserve_invalid(root: Path, cid: str) -> None:
    for folder, suffix in (("cases", "json"), ("details", "npz")):
        path = acq.safe_file(root / folder, f"{cid}.{suffix}")
        if path.exists():
            target = acq.safe_file(root / folder, f"{cid}.{uuid.uuid4().hex}.invalid.{suffix}")
            path.replace(target)


def run_cases(
    *,
    root: Path,
    identity: dict,
    cases: tuple[dict, ...],
    engine: CaseEngine,
    provenance: dict,
    resume: bool = False,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Only fully verified case pairs may be skipped. Cancel between cases."""
    planned_result_rows(cases)  # enforce bound before execution or allocation
    plan = json.loads(canonical_json(cases))
    validate_plan(plan)
    run_id = canonical_hash(identity)
    if engine.run_id != run_id:
        raise DataValidationError("engine identity: mismatch; bind before execution")
    engine.split = identity["split"]
    with writer_lock(root):
        if resume:
            manifest = json_read(root, "run.json")
            required = {
                "identity",
                "run_id",
                "plan",
                "provenance",
                "input_checksums",
                "completed",
                "invalidated",
                "elapsed_sessions_seconds",
            }
            if not required <= manifest.keys() or type(manifest["completed"]) is not dict:
                raise DataValidationError(
                    "resume manifest: invalid schema; restore generated progress"
                )
            if manifest["identity"] != identity or manifest["run_id"] != run_id:
                raise DataValidationError(
                    "resume identity: scientific/data/settings/dependency change; start a new run"
                )
            if manifest["plan"] != plan:
                raise DataValidationError("resume plan: changed IDs; start a new run")
            for name, checksum in manifest["input_checksums"].items():
                parts = name.split("/")
                if len(parts) > 2 or (
                    len(parts) == 2 and parts[0] not in ("grids", "masks", "inputs")
                ):
                    raise DataValidationError("input artifact: unsafe name; restore manifest")
                folder = root if len(parts) == 1 else root / parts[0]
                if not verify_checksum(folder, parts[-1], checksum):
                    raise DataValidationError(
                        "resume input artifact: changed; preserve and diagnose"
                    )
            manifest["provenance"].append(provenance)
        else:
            if acq.safe_file(root, "run.json").exists():
                raise DataValidationError(
                    "run output: exists; use matching resume or a new directory"
                )
            manifest = dict(
                schema_version=1,
                run_id=run_id,
                identity=identity,
                plan=plan,
                plan_hash=canonical_hash(plan),
                expected_rows=len(plan) * 2,
                completed={},
                provenance=[provenance],
                status="incomplete",
                invalidated=[],
                elapsed_sessions_seconds=[],
            )
            manifest["input_checksums"] = {}
            for folder in (root, root / "grids", root / "masks", root / "inputs"):
                if folder.exists():
                    for path in folder.iterdir():
                        if path.is_file() and path.name != "writer.lock":
                            name = path.relative_to(root).as_posix()
                            manifest["input_checksums"][name] = acq.source_sha256(path.read_bytes())
            acq.atomic_write(root, "cases.csv", _plan_csv(plan))
        validate_numeric_inputs(root)
        manifest["status"] = "incomplete"
        json_write(root, "run.json", manifest)
        rows_by_case = {}
        started = perf_counter()
        ordered = sorted(plan, key=lambda c: c["kind"] != "uninjected_real")
        try:
            for case in ordered:
                cid = case["case_id"]
                if cid in manifest["completed"]:
                    try:
                        rows, arrays = read_case(root, case, manifest["completed"][cid], run_id)
                        engine.restore_background(case, rows, arrays)
                        rows_by_case[cid] = rows
                        continue
                    except (DataValidationError, KeyError, TypeError) as exc:
                        manifest["invalidated"].append(
                            dict(case_id=cid, reason=type(exc).__name__, recorded_utc=acq.utc_now())
                        )
                        _preserve_invalid(root, cid)
                        del manifest["completed"][cid]
                        json_write(root, "run.json", manifest)
                if cancel and cancel():
                    break
                if any(
                    acq.safe_file(root / folder, f"{cid}.{suffix}").exists()
                    for folder, suffix in (("cases", "json"), ("details", "npz"))
                ):
                    _preserve_invalid(root, cid)
                    manifest["invalidated"].append(
                        dict(
                            case_id=cid, reason="uncommitted_case_files", recorded_utc=acq.utc_now()
                        )
                    )
                case_start = perf_counter()
                rows, arrays = engine.compute(case)
                validate_rows(rows, [case])
                checkpoint = _save_case(root, case, rows, arrays)
                if case["kind"] == "uninjected_real" and any(
                    r["status"] == "artifact_error" for r in rows
                ):
                    for row in rows:
                        bound = engine.backgrounds[row["background_binding"]]
                        bound.processed = None
                        bound.status, bound.reason = "artifact_error", row["reason"]
                checkpoint["wall_seconds"] = perf_counter() - case_start
                manifest["completed"][cid] = checkpoint
                rows_by_case[cid] = rows
                manifest["actual_rows"] = len(manifest["completed"]) * 2
                json_write(root, "run.json", manifest)
                acq.atomic_write(
                    root / "logs",
                    "run.log",
                    canonical_json(
                        dict(
                            run_id=run_id,
                            completed_cases=len(manifest["completed"]),
                            planned_cases=len(plan),
                            recorded_utc=acq.utc_now(),
                            status="incomplete",
                        )
                    )
                    + b"\n",
                )
                if progress:
                    progress(len(manifest["completed"]), len(plan))
            rows = [row for case in plan for row in rows_by_case.get(case["case_id"], [])]
            if len(rows) == len(plan) * 2:
                validate_rows(rows, plan)
                acq.atomic_write(root, "results.csv", results_csv(rows))
                json_write(root, "results.json", dict(rows=rows))
                manifest["file_checksums"] = {
                    name: acq.source_sha256(acq.safe_file(root, name).read_bytes())
                    for name in ("cases.csv", "results.csv", "results.json")
                }
                # Every committed case is verified again before completion.
                for case in plan:
                    read_case(root, case, manifest["completed"][case["case_id"]], run_id)
                manifest["status"] = "complete"
                manifest["finished_utc"] = acq.utc_now()
        finally:
            manifest["elapsed_sessions_seconds"].append(perf_counter() - started)
            manifest["actual_rows"] = len(manifest["completed"]) * 2
            json_write(root, "run.json", manifest)
        return manifest


def _plan_csv(plan: list[dict]) -> bytes:
    fields = (
        "case_id",
        "case_kind",
        "parent_prepared_hash",
        "mask_name",
        "mask_hash",
        "seed",
        "sigma",
        "replicate",
        "true_period_days",
        "true_epoch_days",
        "true_duration_days",
        "true_depth",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fields, lineterminator="\n")
    writer.writeheader()
    for case in plan:
        row = dict(
            case_id=case["case_id"],
            case_kind=case["kind"],
            parent_prepared_hash=case["prepared_hash"],
            mask_name=case["mask_definition"],
            mask_hash=case["mask_hash"],
            seed=case.get("seed"),
            sigma=case.get("sigma"),
            replicate=case.get("replicate"),
        )
        for key in ("period_days", "epoch_days", "duration_days", "depth"):
            row[f"true_{key}"] = case["injection"][key] if case["injection"] else None
        canonical_json(row)
        writer.writerow(row)
    return stream.getvalue().encode()


def experiment(
    config: ProtocolConfig,
    *,
    repository: Path,
    config_path: Path,
    manifest_path: Path,
    cache: Path,
    output: Path,
    split: str,
    resume: bool = False,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    if split == "evaluation" and not config.frozen:
        raise DataValidationError("evaluation: G2 review/freeze required; run development only")
    manifest, curves = load_split(config, manifest_path, cache, split)
    cases = enumerate_experiment(manifest, curves, config, split)
    identity = run_identity(
        repository,
        settings=config,
        data=dict(manifest=manifest, prepared={k: c.content_hash for k, c in curves.items()}),
        split=split,
    )
    engine = CaseEngine(config, curves, "pending")
    identity["derived"] = dict(
        grids={k: g.checksum for k, g in engine.grids.items()},
        masks={canonical_hash(k): m.checksum for k, m in engine.masks.items()},
    )
    # Convert all dataclasses to the same canonical JSON types used on resume.
    identity = json.loads(canonical_json(identity))
    engine.run_id = canonical_hash(identity)
    provenance = execution_provenance(repository)
    if config.frozen and provenance["dirty"]:
        raise DataValidationError("frozen source: dirty; commit reviewed changes first")
    if split == "evaluation":
        validate_freeze(repository, identity, cases)
    # Snapshot artifacts are content checked on resume, never silently refreshed.
    snapshots = {
        "protocol.toml": config_path.read_bytes(),
        "data_manifest.json": canonical_json(manifest),
    }
    if not resume:
        acq.safe_file(output, "run.json")
        if output.exists() and any(output.iterdir()):
            raise DataValidationError("output directory: not empty; preserve existing outputs")
        for name, content in snapshots.items():
            acq.atomic_write(output, name, content)
        numeric_files = {}
        for key, curve in engine.curves.items():
            name = f"{canonical_hash(key)}.npz"
            numeric_files[f"inputs/{name}"] = numeric_write(
                output / "inputs",
                name,
                dict(
                    time_days=curve.time_days,
                    flux=curve.flux,
                    flux_err=curve.flux_err,
                    support_counts=curve.support_counts,
                ),
            )
        for key, grid in engine.grids.items():
            name = f"{canonical_hash(key)}.npz"
            numeric_files[f"grids/{name}"] = numeric_write(
                output / "grids", name, {"period_days": grid.periods_days}
            )
        for (key, name), mask in engine.masks.items():
            filename = f"{canonical_hash([key, name])}.npz"
            numeric_files[f"masks/{filename}"] = numeric_write(
                output / "masks", filename, {"keep": mask.keep}
            )
        json_write(output, "numeric_inputs.json", dict(files=numeric_files))
    else:
        for name, content in snapshots.items():
            if not verify_checksum(output, name, acq.source_sha256(content)):
                raise DataValidationError("resume snapshot: changed; preserve and diagnose")
    return run_cases(
        root=output,
        identity=identity,
        cases=cases,
        engine=engine,
        provenance=provenance,
        resume=resume,
        cancel=cancel,
        progress=progress,
    )


def validate_freeze(repository: Path, identity: dict, cases: tuple[dict, ...]) -> None:
    """Read a separately reviewed G2 record; this function cannot approve a gate.

    G2 creates the human Markdown record AND this machine-readable companion
    after review/fixes. Neither record exists or is manufactured by T06.
    """
    freeze = json_read(repository / "docs", "PROTOCOL_FREEZE.json")
    review = acq.safe_file(repository / "docs/reviews", "G2.md")
    record = acq.safe_file(repository / "docs", "PROTOCOL_FREEZE.md")
    if (
        freeze.get("g2_verdict") != "PASS"
        or freeze.get("evaluation_identity") != identity
        or freeze.get("planned_rows_hash") != canonical_hash(planned_result_rows(cases))
        or not review.is_file()
        or not record.is_file()
        or freeze.get("review_report_sha256") != acq.source_sha256(review.read_bytes())
        or freeze.get("freeze_record_sha256") != acq.source_sha256(record.read_bytes())
    ):
        raise DataValidationError(
            "evaluation freeze: missing/mismatched review record; complete G2 first"
        )
