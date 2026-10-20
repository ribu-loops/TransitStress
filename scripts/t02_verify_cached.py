"""Repeat actual T02 pinned acquisition/QC with network calls forbidden."""

import runpy
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

from astropy.utils import iers

from transitstress.acquisition import (
    cache_name,
    fetch,
    read_json,
    require_final_manifest,
    utc_now,
    write_json,
)
from transitstress.archive import MastArchive, source_sha256
from transitstress.config import load_config
from transitstress.errors import DataValidationError


def blocked(*args: object, **kwargs: object) -> None:
    """Any attempted socket/DNS operation fails, including library fallbacks."""
    raise AssertionError("T02 offline repeat attempted network access")


def digest(path: Path) -> str:
    return source_sha256(path.read_bytes())


def verify() -> dict:
    """Verify real pinned bytes, unchanged accounting and isolated cache failures."""
    config = load_config(Path("configs/protocol_v1.toml"))
    data, cache = Path("data"), Path(".cache/transitstress")
    manifest = read_json(data, "manifest.json")
    journal = read_json(data, "cohort_acquisition.json")
    paths = [
        data / name
        for name in (
            "acquisition.json",
            "candidate_inventory.json",
            "candidate_inventory_rejected_paging.json",
            "t02_cached_source_evidence.json",
            "cohort_acquisition.json",
            "manifest.json",
        )
    ] + [
        Path("docs/reviews/T02_COHORT.md"),
        Path("docs/reviews/T02_COHORT_REREVIEW.md"),
        Path("configs/t02_cohort_v1.json"),
        Path("configs/protocol_v1.toml"),
        Path("uv.lock"),
    ]
    paths += sorted(cache.glob("*.fits")) + sorted(cache.glob("*.npz"))
    before = {p.as_posix(): digest(p) for p in paths}
    ledger = cache / "pinned_downloads.json"
    ledger_before = ledger.read_bytes() if ledger.exists() else None
    report = fetch(config, data_root=data, cache_root=cache, archive=MastArchive(offline=True))
    if report["downloads"] != 0 or report["bytes_received"] != 0 or report["products"] != 8:
        raise AssertionError("Offline pinned fetch did not verify all eight without I/O")
    runpy.run_path("scripts/t02_qc.py", run_name="__main__")
    if before != {p.as_posix(): digest(p) for p in paths}:
        raise AssertionError("Pinned evidence/source/preparation bytes changed")
    if ledger_before != (ledger.read_bytes() if ledger.exists() else None):
        raise AssertionError("Pinned reservation ledger changed")
    if journal["downloads_total"] != 5 + len(journal["reservations"]):
        raise AssertionError("Historical/new reservation accounting disagrees")
    try:
        require_final_manifest(manifest)
    except DataValidationError as error:
        provisional_refusal = str(error)
    else:
        raise AssertionError("Provisional manifest was accepted for benchmark use")
    # Copies use the actual manifest; never remove/corrupt production cached files.
    failures = {}
    with tempfile.TemporaryDirectory(dir=".cache/tmp", prefix="t02-offline-") as temporary:
        root = Path(temporary)
        write_json(root / "data", "manifest.json", manifest)
        isolated_cache = root / "cache"
        for mode in ("missing", "corrupt"):
            if mode == "corrupt":
                (isolated_cache / cache_name(manifest["products"][0])).write_bytes(b"invalid")
            try:
                fetch(
                    config,
                    data_root=root / "data",
                    cache_root=isolated_cache,
                    archive=MastArchive(offline=True),
                )
            except DataValidationError as error:
                failures[mode] = str(error)
            else:
                raise AssertionError(f"Offline {mode} cache was accepted")
            if (isolated_cache / "pinned_downloads.json").exists():
                raise AssertionError("Offline failure spent a reservation")
    return {
        "status": "pass",
        "verified_utc": utc_now(),
        "network": "socket connect/connect_ex/create_connection/getaddrinfo blocked; IERS off",
        "fetch": report,
        "reservations_before": journal["downloads_total"],
        "reservations_after": read_json(data, "cohort_acquisition.json")["downloads_total"],
        "pinned_ledger_unchanged": True,
        "provisional_benchmark_refusal": provisional_refusal,
        "isolated_offline_failures": failures,
        "unchanged_sha256": before,
        "qc_evidence": read_json(Path("reports"), "t02_qc_evidence.json"),
    }


def main() -> None:
    iers.conf.auto_download = False
    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        evidence = verify()
        write_json(Path("reports"), "t02_offline_repeat.json", evidence)
    print("PASS: eight pinned products/QC; zero network bytes/downloads/new reservations")


if __name__ == "__main__":
    main()
