"""Finalize/reverify T03 on exact cached sources with sockets and DNS blocked."""

import json
import socket
from pathlib import Path
from unittest.mock import patch

from astropy.utils import iers

from transitstress.acquisition import (
    fetch,
    read_json,
    require_final_manifest,
    utc_now,
    write_json,
)
from transitstress.archive import MastArchive, source_sha256
from transitstress.config import load_config
from transitstress.eligibility import finalize_eligibility
from transitstress.errors import DataValidationError


def blocked(*args: object, **kwargs: object) -> None:
    raise AssertionError("T03 offline finalization attempted network access")


def verify() -> dict:
    config = load_config(Path("configs/protocol_v1.toml"))
    data, cache = Path("data"), Path(".cache/transitstress")
    # The manifest and new eligibility evidence are the only intentional data changes.
    protected = [
        p
        for p in data.glob("*.json")
        if p.name not in ("manifest.json", "t03_eligibility.json", "t03_blocked.json")
    ]
    protected += sorted(cache.glob("*.fits")) + sorted(cache.glob("*.npz"))
    protected += [
        Path("configs/protocol_v1.toml"),
        Path("configs/t02_cohort_v1.json"),
        Path("uv.lock"),
        Path("docs/reviews/T02_COHORT.md"),
        Path("docs/reviews/T02_COHORT_REREVIEW.md"),
    ]
    ledger = cache / "pinned_downloads.json"
    if ledger.exists():
        protected.append(ledger)
    before = {p.as_posix(): source_sha256(p.read_bytes()) for p in protected}
    result = finalize_eligibility(config, data_root=data, cache_root=cache)
    manifest_bytes = (data / "manifest.json").read_bytes()
    record_bytes = (data / "t03_eligibility.json").read_bytes()
    repeated = finalize_eligibility(config, data_root=data, cache_root=cache)
    if (
        repeated != result
        or manifest_bytes != (data / "manifest.json").read_bytes()
        or record_bytes != (data / "t03_eligibility.json").read_bytes()
    ):
        raise AssertionError("T03 finalization repeat changed evidence")
    manifest = read_json(data, "manifest.json")
    require_final_manifest(manifest)
    # The preserved provisional snapshot must still fail the benchmark guard.
    try:
        require_final_manifest(read_json(data, "t03_eligibility.json")["input_manifest"])
    except DataValidationError:
        pass
    else:
        raise AssertionError("Provisional snapshot passed benchmark guard")
    fetched = fetch(config, data_root=data, cache_root=cache, archive=MastArchive(offline=True))
    if fetched["downloads"] != 0 or fetched["bytes_received"] != 0 or fetched["products"] != 8:
        raise AssertionError("Final pinned cache repeat did not remain offline")
    if manifest_bytes != (data / "manifest.json").read_bytes():
        raise AssertionError("Pinned fetch changed final manifest")
    if before != {p.as_posix(): source_sha256(p.read_bytes()) for p in protected}:
        raise AssertionError("T03 changed protected scientific/historical evidence")
    return {
        "status": "pass",
        "verified_utc": utc_now(),
        "finalization": result,
        "repeat_identical": True,
        "pinned_fetch": fetched,
        "network": "sockets/DNS blocked; Astropy IERS downloads disabled",
        "searches_executed": 0,
        "unchanged_sha256": before,
        "manifest_sha256": source_sha256(manifest_bytes),
        "eligibility_file_sha256": source_sha256(record_bytes),
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
        write_json(Path("reports"), "t03_offline_verification.json", evidence)
    print(json.dumps(evidence["finalization"], indent=2, allow_nan=False))
    print("PASS: identical support/masks/pins; reservations unchanged; no downloads or searches")


if __name__ == "__main__":
    main()
