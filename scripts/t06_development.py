"""Authorized development-only runtime/evidence driver; never evaluates held-out data."""

import json
import socket
from collections import Counter
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np
from astropy.utils import iers

from transitstress import acquisition as acq
from transitstress.artifacts import json_read, json_write, read_results_csv
from transitstress.config import load_config
from transitstress.controls import enumerate_experiment, planned_result_rows
from transitstress.experiment import experiment, load_split
from transitstress.identity import file_hash, hardware_facts, scientific_inventory
from transitstress.provenance import canonical_hash
from transitstress.reporting import analyze, validate_artifact
from transitstress.workbench import single_case


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def blocked(*args: object, **kwargs: object) -> None:
    raise AssertionError("T06 development driver attempted a network operation")


def science_projection(rows: list[dict]) -> list[dict]:
    # Reproduction tolerances apply to science, not wall clocks/container bytes.
    result = []
    for row in rows:
        value = dict(row)
        for key in ("detrend_seconds", "search_seconds", "elapsed_seconds"):
            value.pop(key, None)
        value["search"] = {k: v for k, v in row["search"].items() if k != "elapsed_seconds"}
        result.append(value)
    return result


def main() -> None:
    root = Path.cwd()
    config_path, manifest_path = Path("configs/protocol_v1.toml"), Path("data/manifest.json")
    cache = Path(".cache/transitstress")
    config = load_config(config_path)
    # Unique invocation folder: every previous output remains available.
    output = Path("runs") / ("t06_" + acq.utc_now().replace(":", ""))
    require(not output.exists(), "Refuse an existing evidence invocation directory")
    before = json_read(Path(".cache"), "t06_before.json")
    entry_source = scientific_inventory(root)
    counters = dict(searches=0, injected_vectors=0, gaussian_vectors=0)
    import transitstress.experiment as engine

    original_search, original_inject, original_gaussian = (
        engine.search_bls,
        engine.inject_box,
        engine.gaussian_control,
    )
    manifest, curves = load_split(config, manifest_path, cache, "development")
    dev_ids = {curve.curve_id for curve in curves.values()}

    def search(*args: object, **kwargs: object) -> object:
        counters["searches"] += 1
        return original_search(*args, **kwargs)

    def inject(curve: object, *args: object, **kwargs: object) -> object:
        require(curve.curve_id in dev_ids, "Attempt to inject a non-development parent")
        counters["injected_vectors"] += 1
        return original_inject(curve, *args, **kwargs)

    def gaussian(curve: object, *args: object, **kwargs: object) -> object:
        require(curve.curve_id in dev_ids, "Attempt to generate a non-development null")
        counters["gaussian_vectors"] += 1
        return original_gaussian(curve, *args, **kwargs)

    with (
        patch.object(engine, "search_bls", search),
        patch.object(engine, "inject_box", inject),
        patch.object(engine, "gaussian_control", gaussian),
    ):
        timings, local = [], []
        for label in ("cold", "repeat"):
            started = perf_counter()
            result = single_case(
                config,
                repository=root,
                output=output / label,
                built_in="8195886",
                period_days=3.41,
                depth_ppm=750.0,
                duration_hours=3.0,
                epoch_fraction=0.43,
            )
            timings.append(
                dict(
                    label=label,
                    wall_seconds=perf_counter() - started,
                    scientific_sessions_seconds=result["elapsed_sessions_seconds"],
                    searches=4,
                    includes="pinned preparation, four searches, serialization, "
                    "validation, plots and ZIP; excludes interpreter/import startup",
                )
            )
            local.append(validate_artifact(output / label)[1])
            print(label, timings[-1]["wall_seconds"], "seconds", flush=True)
        require(
            science_projection(local[0]) == science_projection(local[1]),
            "Cold/repeat science differs (exact comparison, stronger than rtol 1e-8/atol 1e-10)",
        )
        progress = []
        arguments = dict(
            config=config,
            repository=root,
            config_path=config_path,
            manifest_path=manifest_path,
            cache=cache,
            output=output / "development",
            split="development",
        )
        started = perf_counter()
        partial = experiment(
            **arguments,
            cancel=lambda: len(progress) >= 12,
            progress=lambda done, total: progress.append(done),
        )
        sample_seconds = perf_counter() - started
        require(
            partial["status"] == "incomplete"
            and partial["actual_rows"] == 24
            and partial["expected_rows"] == 156,
            "Interrupted prefix lost the full plan",
        )
        json_write(output, "interrupted_progress.json", partial)
        print("Interrupted after 12/78 cases:", sample_seconds, "seconds", flush=True)
        first_checksums = {cid: c["checksum"] for cid, c in partial["completed"].items()}
        started = perf_counter()
        complete = experiment(
            **arguments,
            resume=True,
            progress=lambda done, total: print(f"Development {done}/{total}", flush=True),
        )
        resume_seconds = perf_counter() - started
        require(
            complete["status"] == "complete" and complete["actual_rows"] == 156,
            "Development does not contain all 156 planned rows",
        )
        require(
            all(
                complete["completed"][cid]["checksum"] == checksum
                for cid, checksum in first_checksums.items()
            ),
            "Verified prefix was recomputed",
        )
    run, rows = validate_artifact(output / "development")
    summary = analyze(output / "development")
    parsed = read_results_csv((output / "development/results.csv").read_bytes())
    require(len(parsed) == 156, "CSV roundtrip lost a planned row")
    for actual, expected in zip(parsed, rows, strict=True):
        for key, value in actual.items():
            original = expected.get(key)
            if type(original) is dict:
                original = original["value"]
            require(value == original, "CSV numeric projection changed a value")
    # Evaluation enumeration is metadata-only, with science executors forbidden.
    with (
        patch.object(engine, "search_bls", blocked),
        patch.object(engine, "inject_box", blocked),
        patch.object(engine, "gaussian_control", blocked),
    ):
        _, evaluation_curves = load_split(config, manifest_path, cache, "evaluation")
        evaluation_cases = enumerate_experiment(manifest, evaluation_curves, config, "evaluation")
        evaluation_plan = planned_result_rows(evaluation_cases)
    require(len(evaluation_plan) == 390, "Held-out metadata plan changed")
    require(
        counters == dict(searches=164, injected_vectors=56, gaussian_vectors=6),
        "Unexpected execution counts: background/null reuse may be broken",
    )
    require(scientific_inventory(root) == entry_source, "Scientific source changed during timing")
    for name, checksum in before.items():
        require(file_hash(Path(name)) == checksum, "Protected entry artifact changed")
    require(manifest["downloads_total"] == 9, "Reservation accounting changed")
    sample = list(partial["completed"].values())
    case_seconds = [c["wall_seconds"] for c in complete["completed"].values()]
    safety = 2.0
    measured_development = sample_seconds + resume_seconds
    projected = (
        max(measured_development * 195 / 78, sample_seconds * 195 / 12, max(case_seconds) * 195)
        * safety
    )
    report = dict(
        schema_version=1,
        recorded_utc=acq.utc_now(),
        kind="development_only_not_G2",
        output_directory=output.as_posix(),
        run_id=run["run_id"],
        source_hash=canonical_hash(entry_source),
        producing_provenance=run["provenance"],
        hardware=hardware_facts(),
        cold_repeat=timings,
        cold_repeat_science_exactly_equal=True,
        tolerances=dict(rtol=1e-8, atol=1e-10),
        sampling=dict(
            cases=12,
            result_rows=24,
            seconds=sample_seconds,
            per_case_seconds=[c["wall_seconds"] for c in sample],
            kinds=dict(
                Counter(c["kind"] for c in partial["plan"] if c["case_id"] in partial["completed"])
            ),
        ),
        development=dict(
            cases=78,
            planned_rows=156,
            actual_rows=len(rows),
            row_counts=summary["row_counts"],
            statuses=summary["statuses"],
            summary=summary,
            total_wall_seconds=measured_development,
            resumed_wall_seconds=resume_seconds,
            case_seconds=case_seconds,
            median_case_seconds=float(np.median(case_seconds)),
            max_case_seconds=max(case_seconds),
        ),
        evaluation_metadata=dict(
            planned_rows=390,
            planned_rows_hash=canonical_hash(evaluation_plan),
            case_counts=dict(Counter(c["kind"] for c in evaluation_cases)),
            searches=0,
            injections=0,
        ),
        runtime_projection=dict(
            evaluation_seconds=projected,
            safety_factor=safety,
            method="max(full-development scaling, 12-case prefix scaling, "
            "slowest-case scaling) times 2",
            target_seconds=7200,
            target_met_by_projection=projected <= 7200,
            local_target_seconds=30,
            local_target_met=all(t["wall_seconds"] <= 30 for t in timings),
            limitations="Extrapolation only; held-out runtime and demo navigation not measured.",
        ),
        counters=counters,
        protected_files=len(before),
        protected_files_unchanged=True,
        entry_snapshot_sha256=file_hash(Path(".cache/t06_before.json")),
        downloads_total=9,
        new_reservations=0,
        network="socket and DNS blocked, IERS downloads disabled",
        freeze_created=False,
        g2_approved=False,
    )
    json_write(Path("reports"), "t06_development_evidence.json", report)
    # Small all-row scientific exports are reviewable; bulk arrays remain ignored.
    for name in ("cases.csv", "results.csv", "results.json", "summary.json", "summary.csv"):
        acq.atomic_write(
            Path("reports/t06_development"), name, (output / "development" / name).read_bytes()
        )
    json_write(
        Path("reports/t06_development"),
        "identity.json",
        dict(
            identity=run["identity"],
            run_id=run["run_id"],
            plan_hash=run["plan_hash"],
            expected_rows=156,
            actual_rows=156,
            plan=run["plan"],
            file_checksums=run["file_checksums"],
        ),
    )
    print(json.dumps(report["runtime_projection"], indent=2, allow_nan=False), flush=True)
    print("Development complete. No evaluation execution or G2 approval.", flush=True)


if __name__ == "__main__":
    iers.conf.auto_download = False
    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        main()
