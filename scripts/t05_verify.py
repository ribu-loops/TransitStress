"""Read-only pinned-data T05 enumeration audit; no injected arrays or searches."""

import json
import socket
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from astropy.utils import iers

from transitstress import acquisition as acq
from transitstress.config import load_config
from transitstress.controls import enumerate_experiment, planned_result_rows
from transitstress.masking import make_observation_mask
from transitstress.provenance import canonical_hash, canonical_json
from transitstress.tess_input import prepare_tess


def blocked(*args: object, **kwargs: object) -> None:
    raise AssertionError("T05 offline enumeration attempted network access")


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def verify() -> dict:
    config = load_config(Path("configs/protocol_v1.toml"))
    manifest = acq.read_json(Path("data"), "manifest.json")
    evidence = acq.read_json(Path("data"), "t03_eligibility.json")
    acq.require_final_manifest(manifest)
    acq.validate_manifest(manifest, config)
    curves, pins, masks = {}, {}, {}
    for p in manifest["products"]:
        content = acq.cached_source(Path(".cache/transitstress"), p)
        if content is None:
            raise AssertionError("Pinned cache missing; this audit cannot acquire sources")
        curve, audit = prepare_tess(content, tic_id=p["tic_id"], config=config)
        require(curve.content_hash == p["qc"]["prepared_hash"], "Changed prepared identity")
        require(canonical_hash(audit) == canonical_hash(p["qc"]), "Changed preparation audit")
        require(
            acq.reconcile_processing(p, audit) == p["processing_reconciliation"],
            "Changed processing reconciliation",
        )
        curves[p["tic_id"]] = curve
        saved = next(a for a in evidence["audits"] if a["tic_id"] == p["tic_id"])
        for name in config.masks.names:
            mask = make_observation_mask(curve, name, config.master_seed, config.masks)
            recorded = next(m for m in saved["masks"] if m["definition"] == name)
            require(mask.checksum == recorded["mask_hash"], "Changed saved mask identity")
            masks[f"{p['tic_id']}/{name}"] = mask.checksum
        pins[p["tic_id"]] = dict(
            role=p["role"],
            source_sha256=p["source_sha256"],
            prepared_hash=curve.content_hash,
            original_bounds_days=[curve.original_start_days, curve.original_end_days],
            time_metadata=curve.time_metadata,
        )
    splits = {}
    for split in ("development", "evaluation"):
        cases = enumerate_experiment(manifest, curves, config, split)
        rows = planned_result_rows(cases)
        # Exercise the existing finite JSON encoder; preserve paired row IDs.
        exported = json.loads(canonical_json(rows))
        ids = [(r["case_id"], r["pipeline_id"]) for r in exported]
        require(len(ids) == len(set(ids)), "Duplicated planned row identity")
        require(
            set(Counter(r["case_id"] for r in exported).values()) == {2},
            "Unpaired planned row identity",
        )
        splits[split] = dict(
            case_counts=dict(Counter(c["kind"] for c in cases)),
            result_row_counts=dict(Counter(r["kind"] for r in rows)),
            total_cases=len(cases),
            total_planned_rows=len(rows),
            planned_rows_hash=canonical_hash(rows),
            injection_ids_hash=canonical_hash(
                [c["case_id"] for c in cases if c["kind"] == "injection"]
            ),
        )
    # Snapshot taken before T05 edits: all original artifacts, pins, cached
    # sources, masks, old run files, config/lock and the repaired NPY reader.
    protected = json.loads(Path(".cache/t05_before.json").read_text())
    for name, sha in protected.items():
        require(acq.source_sha256(Path(name).read_bytes()) == sha, "Changed protected file")
    require(manifest["downloads_total"] == 9, "Changed reservation accounting")
    return dict(
        schema_version=1,
        verified_utc=acq.utc_now(),
        status="pass",
        network="socket connect/connect_ex/create_connection and DNS blocked; IERS offline",
        manifest_sha256=acq.source_sha256(Path("data/manifest.json").read_bytes()),
        source_preparation_rechecked=pins,
        saved_mask_hashes=masks,
        splits=splits,
        downloads_total=9,
        new_reservations=0,
        searches_executed=0,
        gaussian_vectors_generated=0,
        injected_arrays_generated=0,
        protected_file_count=len(protected),
        # Avoid machine-specific absolute cache paths in the public audit.
        entry_snapshot_sha256=acq.source_sha256(Path(".cache/t05_before.json").read_bytes()),
        protected_files_unchanged=True,
    )


def main() -> None:
    iers.conf.auto_download = False
    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        result = verify()
        acq.write_json(Path("reports"), "t05_offline_verification.json", result)
    print(json.dumps(result["splits"], indent=2, allow_nan=False))
    print("PASS: pinned enumeration only; protected artifacts unchanged; nine spent reservations")


if __name__ == "__main__":
    main()
