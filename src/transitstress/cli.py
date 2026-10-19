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
        "validation": "complete protocol-v1 configuration validation (T01)",
        "manifest_available": Path("data/manifest.json").is_file(),
        "demo_available": Path("demo").is_dir(),
        "cache_available": Path(".cache/transitstress").is_dir(),
        "science": (
            "T01 contracts and T02 input preparation; doctor performs no acquisition or search"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TransitStress environment and configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check local setup without network access")
    check.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    acquire = commands.add_parser(
        "fetch", help="Explicit MAST acquisition or pinned cache verification"
    )
    acquire.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    acquire.add_argument(
        "--metadata-only",
        action="store_true",
        help="Verify a complete observation inventory and stop before product acquisition",
    )
    acquire.add_argument(
        "--rebuild-inventory",
        action="store_true",
        help="Repair a proven incomplete inventory before a manifest, preserving evidence and caps",
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
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["python_supported"] else 1
