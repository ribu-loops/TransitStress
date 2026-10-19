"""Small offline bootstrap CLI."""

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from transitstress import __version__
from transitstress.config import ConfigurationError, app_mode, load_config

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
        "validation": "bootstrap only; full scientific validation pending T01",
        "manifest_available": Path("data/manifest.json").is_file(),
        "demo_available": Path("demo").is_dir(),
        "cache_available": Path(".cache/transitstress").is_dir(),
        "science": "not implemented; no data downloaded or results produced by doctor",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TransitStress bootstrap (T00)")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check local setup without network access")
    check.add_argument("--config", type=Path, default=Path("configs/protocol_v1.toml"))
    args = parser.parse_args(argv)
    try:
        report = doctor(args.config)
    except (ConfigurationError, importlib.metadata.PackageNotFoundError) as error:
        print(f"doctor: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["python_supported"] else 1
