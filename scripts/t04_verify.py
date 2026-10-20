"""Blocked-network T04 evidence: fixtures and separate real sanity, no benchmark."""

import importlib.metadata
import io
import json
import platform
import socket
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.utils import iers

from transitstress import acquisition as acq
from transitstress.config import load_config
from transitstress.models import PreparedCurve, TimeMetadata
from transitstress.sanity import blind_pair, run_sanity
from transitstress.search import build_search_grid


def blocked(*args: object, **kwargs: object) -> None:
    raise AssertionError("T04 offline verification attempted network access")


def fixtures(output_root: Path) -> list[dict]:
    """Handwritten interval generator, separate from production box injection."""
    config = load_config(Path("configs/protocol_v1.toml"))
    ref = acq.read_json(Path("tests/fixtures"), "bls_reference.json")
    time = np.arange(0, ref["baseline_days"], ref["cadence_days"])
    curve = PreparedCurve(
        "synthetic-fixture-not-TESS",
        time,
        np.ones(len(time)),
        np.full(len(time), ref["error"]),
        0.0,
        ref["baseline_days"],
        0.0,
        np.ones(len(time), dtype=int),
        TimeMetadata(None, "unknown", "unknown", "days", "synthetic", 1.0),
        "b" * 64,
    )
    reports = []
    for index, period in enumerate([*ref["periods_days"], None, None]):
        flux = np.ones(len(time))
        if period is not None:
            for event in range(-1, 20):
                center = ref["epoch_days"] + event * period
                flux[(time >= center - 0.0625) & (time < center + 0.0625)] -= ref["depth"]
            kind = "synthetic_off_grid_positive"
        elif index == 2:
            kind = "synthetic_constant_negative"
        else:
            kind = "synthetic_seeded_Gaussian_negative_diagnostic"
            flux += np.random.Generator(np.random.PCG64(5489)).normal(0, ref["error"], len(time))
        grid, output, arrays = blind_pair(replace(curve, flux=flux), config)
        comparisons = {}
        for name, result in output["searches"].items():
            if result["status"] != "ok":
                raise AssertionError("Synthetic fixture search failed technically")
            if period is not None:
                error = abs(result["period_days"] - period) / period
                if period in arrays["period_days"] or error > ref["relative_period_tolerance"]:
                    raise AssertionError("Off-grid positive fixture failed declared tolerance")
                comparisons[name] = {"relative_period_error": error, "within_one_percent": True}
            elif index == 2 and (abs(result["depth"]) > 1e-14 or abs(result["power"]) > 1e-14):
                raise AssertionError("Constant negative fixture acquired spurious depth/power")
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        name = f"synthetic_{index}.npz"
        acq.atomic_write(output_root, name, buffer.getvalue())
        reports.append(
            {
                "kind": kind,
                "truth_period_days_post_search_only": period,
                "grid": grid,
                **output,
                "post_search_comparison": comparisons,
                "numerical_file": name,
                "numerical_file_sha256": acq.source_sha256(buffer.getvalue()),
                "negative_interpretation": None
                if period
                else (
                    "No injected truth; largest BLS peak has no calibrated significance. "
                    "This fixture is not T05/G2 benchmark control execution."
                ),
            }
        )
    return reports


def verify() -> dict:
    config = load_config(Path("configs/protocol_v1.toml"))
    data, cache = Path("data"), Path(".cache/transitstress")
    output_root = Path("reports/t04_sanity")
    protected = [*data.glob("*"), *cache.glob("*.fits"), *cache.glob("*.npz")]
    protected += [
        Path("configs/protocol_v1.toml"),
        Path("configs/t02_cohort_v1.json"),
        Path("uv.lock"),
    ]
    protected += list(Path("docs/reviews").glob("*.md"))
    protected += [Path("reports/T03_COMPLETION.md"), Path("reports/t03_offline_verification.json")]
    if (cache / "pinned_downloads.json").exists():
        protected.append(cache / "pinned_downloads.json")
    before = {p.as_posix(): acq.source_sha256(p.read_bytes()) for p in protected if p.is_file()}
    manifest = acq.read_json(data, "manifest.json")
    acq.validate_manifest(manifest, config)
    acq.require_final_manifest(manifest)
    # Re-run T03's real repeat/pin/support verification in memory. Its own
    # report is not rewritten and no discovery/download capability is used.
    from t03_finalize import verify as verify_t03

    t03 = verify_t03()
    grids = {}
    for product in manifest["products"]:
        content = acq.cached_source(cache, product)
        if content is None:
            raise AssertionError("Missing pinned cache")
        curve = acq.verify_pinned(product, content, config, cache)
        grid = build_search_grid(curve, config.search)
        grids[product["tic_id"]] = {
            "role": product["role"],
            "prepared_hash": curve.content_hash,
            "grid_hash": grid.checksum,
            "length": len(grid.periods_days),
            "original_start_days": curve.original_start_days,
            "original_end_days": curve.original_end_days,
            "original_baseline_days": grid.original_baseline_days,
            "first_period_days": float(grid.periods_days[0]),
            "last_period_days": float(grid.periods_days[-1]),
            "searches_executed": 0,
        }
    synthetic = fixtures(Path(".cache/t04_fixtures"))
    real = run_sanity(
        config, manifest_path=data / "manifest.json", cache_root=cache, output_root=output_root
    )
    if real["status"] != "complete":
        raise AssertionError("Real sanity technical failure; preserve report and diagnose")
    after = {name: acq.source_sha256(Path(name).read_bytes()) for name in before}
    if before != after:
        raise AssertionError("Protected input/history bytes changed")
    report = {
        "schema_version": 1,
        "verified_utc": acq.utc_now(),
        "network": "socket connect/connect_ex/create_connection and DNS blocked; IERS offline",
        "python": platform.python_version(),
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("numpy", "astropy", "matplotlib")
        },
        "settings": asdict(config.search),
        "t03_reverification": t03["finalization"],
        "reservations_before": manifest["downloads_total"],
        "reservations_after": acq.read_json(data, "cohort_acquisition.json")["downloads_total"],
        "downloads": 0,
        "development_benchmark_searches": 0,
        "evaluation_searches": 0,
        "synthetic_searches": 8,
        "real_known_signal_searches": 2,
        "independent_per_star_grids_without_benchmark_search": grids,
        "synthetic_fixtures": synthetic,
        "real_sanity_report": "t04_sanity/sanity.json",
        "real_sanity_report_sha256": acq.source_sha256((output_root / "sanity.json").read_bytes()),
        "real_period_recovery_acceptance": any(
            x.get("within_one_percent") is True for x in real["post_search_comparison"].values()
        ),
        "unchanged_sha256": before,
        "scientific_source_sha256": {
            p.as_posix(): acq.source_sha256(p.read_bytes())
            for p in sorted(Path("src/transitstress").glob("*.py"))
        },
        "driver_sha256": acq.source_sha256(Path(__file__).read_bytes()),
    }
    acq.write_json(Path("reports"), "t04_offline_verification.json", report)
    return report


def main() -> None:
    iers.conf.auto_download = False
    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        evidence = verify()
    print(
        json.dumps(
            {
                k: evidence[k]
                for k in (
                    "verified_utc",
                    "reservations_before",
                    "reservations_after",
                    "downloads",
                    "synthetic_searches",
                    "real_known_signal_searches",
                    "development_benchmark_searches",
                    "evaluation_searches",
                    "real_period_recovery_acceptance",
                )
            },
            indent=2,
            allow_nan=False,
        )
    )
    if not evidence["real_period_recovery_acceptance"]:
        raise AssertionError(
            "Neither real configuration recovered the period; diagnose, do not tune"
        )


if __name__ == "__main__":
    main()
