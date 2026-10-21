"""Bounded, offline public evidence adapter. No private/shared cache or execution."""

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from transitstress.acquisition import safe_file
from transitstress.artifacts import (
    json_read,
    numeric_read,
    read_results_csv,
    results_csv,
    validate_plan,
    validate_rows,
)
from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash, canonical_json, numeric_hash

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "demo/t10"
TABLES = (
    "group_counts.csv",
    "star_depth_mask.csv",
    "paired_summary.csv",
    "paired_cases.csv",
    "control_diagnostics.csv",
    "sampling.csv",
    "summary.csv",
    "cases.csv",
)
FILES = {
    "results.json",
    "results.csv",
    "identity.json",
    "summary.json",
    "analysis.json",
    "selection.json",
    "plan.json",
    "sanity.json",
    "sanity.png",
    "freeze.json",
    "T07.md",
    *TABLES,
    *(f"example{i}.{suffix}" for i in range(1, 4) for suffix in ("json", "npz", "png")),
}
PIPES = ("rm_0p5d", "rm_1p5d")


@dataclass
class DemoBundle:
    """New validated public objects each load; never accepts upload/session content."""

    manifest: dict
    rows: list[dict]
    identity: dict
    plan: list[dict]
    analysis: dict
    sanity: dict
    arrays: dict[str, dict]
    content: dict[str, bytes]
    fingerprint: str

    def table(self, name: str) -> list[dict]:
        if name not in TABLES:
            raise DataValidationError("table: unsupported; choose packaged evidence")
        return list(csv.DictReader(io.StringIO(self.content[name].decode("utf-8"))))


def counts(rows: list[dict], pipe: str) -> dict:
    """Presentation counts only; use saved scoring, never re-evaluate a search."""
    selected = [r for r in rows if r["case_kind"] == "injection" and r["pipeline_id"] == pipe]
    exact = [r for r in selected if r["exact_recovery"] is True]
    return dict(
        planned=len(selected),
        eligible=sum(r["coverage_eligible"] is True for r in selected),
        exact=len(exact),
        insufficient_coverage=sum(r["coverage_eligible"] is False for r in selected),
        technical_failures=sum(r["status"] != "ok" for r in selected),
        exact_with_background=sum(r["background_already_matches"] is True for r in exact),
        exact_without_background=sum(r["background_already_matches"] is False for r in exact),
        exact_background_unknown=sum(r["background_already_matches"] is None for r in exact),
        background_matches_all=sum(r["background_already_matches"] is True for r in selected),
    )


def transport(root: Path) -> tuple[dict, dict[str, bytes]]:
    """Check every fixed public member before either validation or a cache lookup."""
    try:
        meta = json_read(root, "bundle.json")
        if meta["schema_version"] != 1 or meta["protocol"] != "transitstress-v1-cohort1":
            raise ValueError("bundle version")
        if type(meta["files"]) is not dict or set(meta["files"]) != FILES:
            raise ValueError("bundle inventory")
        if any(
            type(r) is not dict
            or type(r.get("bytes")) is not int
            or not 0 < r["bytes"] <= 8 * 1024**2
            or not isinstance(r.get("sha256"), str)
            for r in meta["files"].values()
        ):
            raise ValueError("member record")
        content = {}
        if sum(r["bytes"] for r in meta["files"].values()) > 24 * 1024**2:
            raise ValueError("bundle size")
        for name in sorted(FILES):
            path = safe_file(root, name)
            record = meta["files"][name]
            if (
                not path.is_file()
                or not 0 < path.stat().st_size <= 8 * 1024**2
                or path.stat().st_size != record["bytes"]
            ):
                raise ValueError("member size")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("member checksum")
            if name.endswith(".png") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("image schema")
            content[name] = data
        return meta, content
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise DataValidationError(
            "Demo bundle missing or corrupt. Restore demo/t10 from the verified checkout "
            "and restart; do not fetch data or rerun evaluation to repair presentation."
        ) from error


def load_bundle(root: Path | None = None) -> DemoBundle:
    """Validate all paired rows, numeric payloads and aggregates, without any cache."""
    root = BUNDLE if root is None else root
    try:
        meta, content = transport(root)
        identity = json_read(root, "identity.json")
        run_id = canonical_hash(identity["identity"])
        freeze = json_read(root, "freeze.json")
        if (
            run_id != meta["run_id"]
            or identity["run_id"] != run_id
            or identity["identity"]["split"] != "evaluation"
            or freeze["evaluation_identity"] != identity["identity"]
            or freeze["g2_verdict"] != "PASS"
            or identity["finished_utc"] != meta["finished_utc"]
        ):
            raise ValueError("frozen identity")
        plan = json_read(root, "plan.json")["cases"]
        rows = json_read(root, "results.json")["rows"]
        validate_plan(plan)
        validate_rows(rows, plan)
        if (
            len(rows) != 390
            or len(plan) != 195
            or any(r["run_id"] != run_id or r["split"] != "evaluation" for r in rows)
            or read_results_csv(content["results.csv"]) != read_results_csv(results_csv(rows))
        ):
            raise ValueError("complete results/CSV")
        analysis = json_read(root, "analysis.json")
        groups = list(csv.DictReader(io.StringIO(content["group_counts.csv"].decode())))
        if (
            len(groups) != 42
            or len({(g["pipeline_id"], g["dimension"], g["group"]) for g in groups}) != 42
        ):
            raise ValueError("aggregate inventory")
        for group in groups:
            dimension, value = group["dimension"], group["group"]
            if dimension not in {"all", "star", "depth", "mask", "depth_mask"}:
                raise ValueError("aggregate dimension")

            def label(row: dict) -> str:
                return {
                    "all": "all",
                    "star": row["curve_id"],
                    "depth": str(row["true_depth"]),
                    "mask": row["mask_name"],
                    "depth_mask": str([row["true_depth"], row["mask_name"]]),
                }[dimension]

            selected = [r for r in rows if r["case_kind"] == "injection" and label(r) == value]
            if (
                not selected
                or group["pipeline_id"] not in PIPES
                or any(
                    int(group[k]) != v for k, v in counts(selected, group["pipeline_id"]).items()
                )
            ):
                raise ValueError("aggregate counts")
        arrays = {}
        curated = meta["curated"]
        if len(curated) != 3 or len({c["case_id"] for c in curated}) != 3:
            raise ValueError("curated inventory")
        for index, selected in enumerate(curated, 1):
            cid = selected["case_id"]
            if not re.fullmatch(r"[0-9a-f]{64}", cid) or selected["stem"] != f"example{index}":
                raise ValueError("curated identifier")
            checkpoint = json_read(root, f"example{index}.json")
            pair = [r for r in rows if r["case_id"] == cid]
            if checkpoint["case_id"] != cid or checkpoint["rows"] != pair:
                raise ValueError("curated row binding")
            data = numeric_read(root, f"example{index}.npz", checkpoint["numerical"])
            if (
                len(data["time_days"]) != pair[0]["n_observed"]
                or np.any(np.diff(data["time_days"]) <= 0)
                or numeric_hash(
                    {
                        "time_days": data["time_days"],
                        "flux": data["input_flux"],
                        "flux_err": data["input_flux_err"],
                    }
                )
                != pair[0]["input_hash"]
            ):
                raise ValueError("curated sampling")
            for pipe in PIPES:
                if any(
                    len(data[f"{pipe}_{key}"]) != len(data["time_days"])
                    for key in ("flux", "trend")
                ):
                    raise ValueError("curated series alignment")
            arrays[cid] = data
        sanity = json_read(root, "sanity.json")
        if (
            sanity["kind"] != "real_known_signal_sanity_not_benchmark"
            or sanity["status"] != "complete"
            or sanity["tic_id"] != "261136679"
            or any(sanity["searches"][p]["status"] != "ok" for p in PIPES)
        ):
            raise ValueError("sanity identity")
        return DemoBundle(
            meta, rows, identity, plan, analysis, sanity, arrays, content, canonical_hash(meta)
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        raise DataValidationError(
            "Demo bundle missing or corrupt. Restore demo/t10 from the verified checkout "
            "and restart; do not fetch data or rerun evaluation to repair presentation."
        ) from error


def case_zip(bundle: DemoBundle, case_id: str) -> bytes:
    """Bounded fixed-name evidence for a validated public pair, including summary-only."""
    rows = [r for r in bundle.rows if r["case_id"] == case_id]
    if len(rows) != 2:
        raise DataValidationError("case ID: unavailable; select an existing packaged case")
    members = {
        "results.json": canonical_json({"rows": rows}),
        "results.csv": results_csv(rows),
        "provenance.json": canonical_json(bundle.identity),
        "case.json": canonical_json(next(c for c in bundle.plan if c["case_id"] == case_id)),
    }
    selected = next((c for c in bundle.manifest["curated"] if c["case_id"] == case_id), None)
    if selected:
        stem = selected["stem"]
        members.update(
            {
                "arrays.npz": bundle.content[f"{stem}.npz"],
                "numeric_record.json": bundle.content[f"{stem}.json"],
                "diagnostic.png": bundle.content[f"{stem}.png"],
            }
        )
    members["README.txt"] = (
        "Precomputed frozen evaluation evidence. No new search executed.\n"
        + (
            "Detailed arrays and static diagnostic included.\n"
            if selected
            else "SUMMARY ONLY: detailed evidence unavailable in this demo.\n"
            "Restore the matching original run offline, then use validate-artifact; "
            "do not recompute or mix an exploratory result into frozen evidence.\n"
        )
        + "Time: relative days; flux: normalized dimensionless; depth: relative flux.\n"
        "135 correlated injection cases across five evaluation stars; paired processing.\n"
        "BLS/SNR are diagnostics, not planet confirmation or calibrated probabilities.\n"
    ).encode()
    members["checksums.json"] = canonical_json(
        {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return stream.getvalue()


if __name__ == "__main__":
    from time import perf_counter

    started = perf_counter()
    validated = load_bundle()
    print(
        f"Offline demo validated: {len(validated.rows)} rows, "
        f"{len(validated.arrays)} detailed pairs, separate known signal; "
        f"{perf_counter() - started:.3f} s; {validated.fingerprint}"
    )
