"""One-time T10 packaging of existing evidence; never imported by the dashboard.

Run with locked Python as a module. No acquisition, searches or numerical edits.
The original frozen HTML is deliberately not copied into the presentation bundle.
"""

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from transitstress.artifacts import json_read, numeric_read, validate_plan, validate_rows
from transitstress.provenance import canonical_json

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    run = ROOT / "runs/t07_evaluation_20261020T1857Z"
    reports = ROOT / "reports/t07_evaluation"
    target = ROOT / "demo/t10"
    if target.exists():
        raise ValueError("demo/t10 already exists; preserve it and inspect before repackaging")
    manifest = json_read(run, "run.json")
    rows = json_read(reports, "results.json")["rows"]
    validate_plan(manifest["plan"])
    validate_rows(rows, manifest["plan"])
    selection = json_read(reports, "selection.json")["deduplicated"]
    # Validate every selected original numeric payload before copying any files.
    checkpoints = {}
    for selected in selection:
        cid = selected["case_id"]
        checkpoint = json_read(run / "cases", f"{cid}.json")
        numeric_read(run / "details", f"{cid}.npz", checkpoint["numerical"])
        checkpoints[cid] = checkpoint
    target.mkdir(parents=True)
    sources = {}

    def copy(source: Path, name: str) -> None:
        shutil.copyfile(source, target / name)
        sources[name] = source.relative_to(ROOT).as_posix()

    for name in (
        "results.json",
        "results.csv",
        "cases.csv",
        "identity.json",
        "summary.json",
        "summary.csv",
        "analysis.json",
        "group_counts.csv",
        "star_depth_mask.csv",
        "paired_summary.csv",
        "paired_cases.csv",
        "control_diagnostics.csv",
        "sampling.csv",
        "selection.json",
    ):
        copy(reports / name, name)
    (target / "plan.json").write_bytes(canonical_json({"cases": manifest["plan"]}) + b"\n")
    sources["plan.json"] = "runs/t07_evaluation_20261020T1857Z/run.json#plan"
    for index, selected in enumerate(selection):
        cid = selected["case_id"]
        stem = f"example{index + 1}"
        copy(run / f"cases/{cid}.json", f"{stem}.json")
        copy(run / f"details/{cid}.npz", f"{stem}.npz")
        copy(reports / selected["plot"], f"{stem}.png")
        selected["stem"] = stem
    copy(ROOT / "reports/t04_sanity/sanity.json", "sanity.json")
    copy(ROOT / "reports/t04_sanity/sanity.png", "sanity.png")
    copy(ROOT / "docs/PROTOCOL_FREEZE.json", "freeze.json")
    copy(ROOT / "docs/reviews/T07.md", "T07.md")
    record = dict(
        schema_version=1,
        protocol="transitstress-v1-cohort1",
        packaged_utc=datetime.now(UTC).isoformat(),
        run_id=manifest["run_id"],
        finished_utc=manifest["finished_utc"],
        curated=selection,
        files={
            name: dict(
                bytes=(target / name).stat().st_size,
                sha256=hashlib.sha256((target / name).read_bytes()).hexdigest(),
                source=source,
            )
            for name, source in sources.items()
        },
    )
    (target / "bundle.json").write_bytes(canonical_json(record) + b"\n")
    print(f"Packaged {len(sources)} existing evidence files; no science executed")


if __name__ == "__main__":
    main()
