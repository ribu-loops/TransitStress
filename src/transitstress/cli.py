"""Explicit acquisition and offline environment/protocol checks."""

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from transitstress import __version__
from transitstress.config import app_mode, load_config
from transitstress.errors import TransitStressError

DEPENDENCIES = ("numpy", "pandas", "astropy", "lightkurve", "plotly", "streamlit")


def doctor(config_path: Path) -> dict[str, object]:
    """Report allowlisted environment facts; never download or inspect private files."""
    config = load_config(config_path)
    return {
        "transitstress": __version__,
        "python": platform.python_version(),
        "python_supported": sys.version_info[:2] == (3, 12),
        "os": platform.platform(),
        "mode": app_mode(),
        "dependencies": {name: importlib.metadata.version(name) for name in DEPENDENCIES},
        "protocol_id": config.protocol_id,
        "validation": "schema 2 / protocol-v1-cohort1 configuration validation",
        "cohort_hash": config.selection.cohort_sha256,
        "manifest_available": Path("data/manifest.json").is_file(),
        "demo_available": Path("demo").is_dir(),
        "cache_available": Path(".cache/transitstress").is_dir(),
        "science": ("T04 blind detrending/BLS/sanity; doctor performs no acquisition or search"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TransitStress environment and configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("experiment", help="Offline development paired experiment")
    batch.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    batch.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    batch.add_argument("--split", choices=("development", "evaluation"), required=True)
    batch.add_argument("--output", type=Path)
    batch.add_argument("--resume", type=Path)
    analysis = commands.add_parser("analyze", help="Validate and summarize completed rows")
    analysis.add_argument("--run", type=Path, required=True)
    artifact = commands.add_parser("validate-artifact", help="Verify run IDs, schema and checksums")
    artifact.add_argument("--path", type=Path, required=True)
    case = commands.add_parser("case", help="Exploratory single paired injection, no Streamlit")
    case.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    source = case.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=str)
    source.add_argument("--built-in", choices=("8195886", "8196058"))
    case.add_argument("--confirm-days-linear-units", action="store_true")
    case.add_argument("--period-days", type=float, required=True)
    case.add_argument("--depth-ppm", type=float, required=True)
    case.add_argument("--duration-hours", type=float, required=True)
    case.add_argument("--epoch-fraction", type=float, required=True)
    case.add_argument(
        "--mask", choices=("original", "random_missing", "central_block"), default="original"
    )
    case.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("doctor", help="Check local setup without network access")
    check.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    sanity = commands.add_parser("sanity", help="Offline broad known-signal search; no benchmark")
    sanity.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    sanity.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    sanity.add_argument("--output", type=Path, default=Path("runs/sanity"))
    finalize = commands.add_parser(
        "finalize", help="Offline T03 support validation and final roles"
    )
    finalize.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    acquire = commands.add_parser(
        "fetch", help="Explicit MAST acquisition or pinned cache verification"
    )
    acquire.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    acquire.add_argument(
        "--metadata-only",
        action="store_true",
        help="Verify exact scheduled product metadata; stop before source downloads",
    )
    acquire.add_argument(
        "--rebuild-inventory",
        action="store_true",
        help="Retired: cohort mode refuses sector inventory rebuilds",
    )
    acquire.add_argument(
        "--offline", action="store_true", help="Verify existing cache; never contact MAST"
    )
    acquire.add_argument(
        "--recheck-qc",
        action="store_true",
        help="Recheck exact cached QC failures after a preparation repair; preserve their history",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "case":
            from transitstress.workbench import single_case

            report = single_case(
                load_config(args.config),
                repository=Path.cwd(),
                output=args.output,
                period_days=args.period_days,
                depth_ppm=args.depth_ppm,
                duration_hours=args.duration_hours,
                epoch_fraction=args.epoch_fraction,
                mask_name=args.mask,
                built_in=args.built_in,
                csv_path=args.csv,
                confirm_days_and_linear_units=args.confirm_days_linear_units,
            )
            print(json.dumps({k: report[k] for k in ("run_id", "status", "actual_rows")}))
            return 0 if report["status"] == "complete" and _no_technical_errors(args.output) else 2
        if args.command == "experiment":
            from transitstress.acquisition import utc_now
            from transitstress.experiment import experiment
            from transitstress.reporting import analyze

            if args.resume and args.output:
                raise TransitStressError("output: ambiguous; use --resume or --output")
            output = (
                args.resume
                or args.output
                or Path("runs") / ("development_" + utc_now().replace(":", ""))
            )
            report = experiment(
                load_config(args.config),
                repository=Path.cwd(),
                config_path=args.config,
                manifest_path=args.manifest,
                cache=Path(".cache/transitstress"),
                output=output,
                split=args.split,
                resume=args.resume is not None,
                progress=lambda done, total: print(f"Completed cases {done}/{total}", flush=True),
            )
            if report["status"] == "complete":
                analyze(output)
            print(json.dumps({k: report[k] for k in ("run_id", "status", "actual_rows")}))
            return 0 if report["status"] == "complete" and _no_technical_errors(output) else 2
        if args.command in ("analyze", "validate-artifact"):
            from transitstress.reporting import analyze, validate_artifact

            if args.command == "analyze":
                report = analyze(args.run)
                print(json.dumps({k: report[k] for k in ("run_id", "row_counts", "statuses")}))
            else:
                report, rows = validate_artifact(args.path)
                print(json.dumps(dict(run_id=report["run_id"], validated_rows=len(rows))))
            return 0
        if args.command == "sanity":
            from transitstress.sanity import run_sanity

            report = run_sanity(
                load_config(args.config),
                manifest_path=args.manifest,
                cache_root=Path(".cache/transitstress"),
                output_root=args.output,
            )
            print(
                json.dumps(
                    {
                        "kind": report["kind"],
                        "status": report["status"],
                        "grid": report["grid"],
                        "searches": report["searches"],
                        "post_search_comparison": report["post_search_comparison"],
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
            return 0 if report["status"] == "complete" else 2
        if args.command == "finalize":
            from transitstress.eligibility import finalize_eligibility

            report = finalize_eligibility(
                load_config(args.config),
                data_root=Path("data"),
                cache_root=Path(".cache/transitstress"),
            )
            print(json.dumps(report, indent=2, allow_nan=False))
            return 0
        if args.command == "fetch":
            from transitstress.acquisition import fetch, write_selection_csv
            from transitstress.archive import MastArchive

            report = fetch(
                load_config(args.config),
                data_root=Path("data"),
                cache_root=Path(".cache/transitstress"),
                archive=MastArchive(offline=args.offline),
                recheck_qc=args.recheck_qc,
                rebuild_inventory=args.rebuild_inventory,
                metadata_only=args.metadata_only,
            )
            write_selection_csv(Path("data"))
            print(json.dumps(report, indent=2, allow_nan=False))
            return 0
        report = doctor(args.config)
    except (TransitStressError, importlib.metadata.PackageNotFoundError) as error:
        print(f"{args.command}: {error}", file=sys.stderr)
        return 2
    except OSError:
        print(
            f"{args.command}: local file operation failed; preserve output and check access",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["python_supported"] else 1


def _no_technical_errors(root: Path) -> bool:
    from transitstress.artifacts import json_read

    return all(row["status"] == "ok" for row in json_read(root, "results.json")["rows"])
