"""Deterministic, journaled T02 acquisition; no injected or benchmark searches."""

import csv
import io
import json
import math
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from transitstress.archive import (
    FILENAME,
    MAX_METADATA,
    MAX_PRODUCT,
    MastArchive,
    product_uri,
    source_sha256,
)
from transitstress.config import ProtocolConfig
from transitstress.errors import DataValidationError
from transitstress.models import PreparedCurve
from transitstress.provenance import canonical_hash, canonical_json
from transitstress.tess_input import TessQCError, basic_qc_reasons, prepare_tess

OBS_COLUMNS = (
    "obsid",
    "obs_id",
    "target_name",
    "obs_collection",
    "provenance_name",
    "sequence_number",
    "t_exptime",
    "dataproduct_type",
    "dataRights",
    "t_obs_release",
)
QUERY = {
    "columns": ",".join(OBS_COLUMNS),
    "filters": [
        {"paramName": "obs_collection", "values": ["TESS"]},
        {"paramName": "provenance_name", "values": ["SPOC"]},
        {"paramName": "sequence_number", "values": [{"min": 1, "max": 1}]},
        {"paramName": "t_exptime", "values": [{"min": 120, "max": 120}]},
        {"paramName": "dataproduct_type", "values": ["timeseries"]},
        {"paramName": "dataRights", "values": ["PUBLIC"]},
    ],
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def input_settings_hash(config: ProtocolConfig) -> str:
    """Acquisition settings only; later G2 freeze/seed/search changes are not reselection."""
    return canonical_hash(
        {
            "data": config.data,
            "sanity": config.sanity,
            "preparation": config.preparation,
            "limits": config.limits,
        }
    )


def safe_file(root: Path, name: str) -> Path:
    """Generated flat names only, under explicit roots; reject symlink escapes."""
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in (".", ".."):
        raise DataValidationError("output name: unsafe; use a generated internal basename")
    if (
        root.is_symlink()
        or root.is_junction()
        or any(p.is_symlink() or p.is_junction() for p in root.parents)
    ):
        raise DataValidationError(
            "output root: symlink/junction rejected; choose a regular local directory"
        )
    root = root.resolve()
    target = root / name
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise DataValidationError("output path: escape rejected; preserve root containment")
    return target


def atomic_write(root: Path, name: str, content: bytes) -> None:
    target = safe_file(root, name)
    root.mkdir(parents=True, exist_ok=True)
    temporary = safe_file(root, f"{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(root: Path, name: str, value: object) -> None:
    atomic_write(root, name, canonical_json(value) + b"\n")


def read_json(root: Path, name: str) -> dict:
    target = safe_file(root, name)
    try:
        with target.open("rb") as stream:
            content = stream.read(MAX_METADATA + 1)
        if len(content) > MAX_METADATA:
            raise ValueError("oversized")
        value = json.loads(content)
        if type(value) is not dict:
            raise ValueError("not an object")
        canonical_json(value)  # rejects NaN/Infinity and unsupported values
        return value
    except (OSError, ValueError) as error:
        raise DataValidationError(
            "acquisition JSON: missing/corrupt/oversized; restore the rec"
            "orded file, do not reselect"
        ) from error


def candidate_inventory(rows: list[dict], sanity_tic: str) -> dict:
    """Validate all returned observations before numerical TIC ordering/deduplication."""
    groups: dict[str, list[dict]] = {}
    excluded = []
    for row in rows:
        try:
            tic = str(row["target_name"])
            valid = (
                tic.isascii()
                and tic.isdigit()
                and int(tic) > 0
                and row["obs_collection"] == "TESS"
                and row["provenance_name"] == "SPOC"
                and row["sequence_number"] == 1
                and row["t_exptime"] == 120
                and row["dataproduct_type"].lower() == "timeseries"
                and row["dataRights"] == "PUBLIC"
                and str(row["obsid"]).isdigit()
                and isinstance(row["obs_id"], str)
                and type(row["t_obs_release"]) in (int, float)
                and math.isfinite(row["t_obs_release"])
            )
        except (KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise DataValidationError(
                "observation inventory: metadata contradicts fixed query; inspect archive response"
            )
        tic = str(int(tic))
        clean = {name: row[name] for name in OBS_COLUMNS}
        if clean not in groups.setdefault(tic, []):
            groups[tic].append(clean)
    ordered = [
        {"tic_id": tic, "observations": sorted(groups[tic], key=lambda r: str(r["obsid"]))}
        for tic in sorted(groups, key=int)
    ]
    if sanity_tic not in groups:
        raise DataValidationError(
            "sanity TIC: absent from fixed inventory; investigate without substituting source"
        )
    excluded.append({"tic_id": sanity_tic, "reason": "separate sanity TIC excluded from benchmark"})
    return {
        "schema_version": 1,
        "query": QUERY,
        "acquired_utc": utc_now(),
        "observations_returned": len(rows),
        "candidates": ordered,
        "exclusions": excluded,
    }


def choose_product(rows: list[dict], candidate: dict) -> tuple[dict, list[dict]]:
    """Newest documented archive release, version when available, filename tie-break.

    The filename's four-digit spacecraft configuration ID is NOT a pipeline version.
    Preserve all considered products and rejection reasons, including non-LC files.
    """
    observations = {str(row["obsid"]): row for row in candidate["observations"]}
    eligible: dict[str, dict] = {}
    evidence = []
    for row in rows:
        name = row.get("productFilename")
        uri = row.get("dataURI")
        obs = observations.get(str(row.get("parent_obsid"))) or observations.get(
            str(row.get("obsID"))
        )
        reason = None
        if not isinstance(name, str) or not FILENAME.fullmatch(name):
            reason = "not a sector-1 nominal short-cadence SPOC light-curve filename"
        elif int(name.split("-")[2]) != int(candidate["tic_id"]):
            reason = "different TIC"
        elif (
            row.get("obs_collection") != "TESS"
            or row.get("dataRights") != "PUBLIC"
            or row.get("productType") != "SCIENCE"
            or obs is None
        ):
            reason = "mission/public/science/parent metadata mismatch"
        elif uri != "mast:TESS/product/" + name:
            reason = "unexpected persistent product URI"
        elif type(row.get("size")) is not int or not 0 < row["size"] <= MAX_PRODUCT:
            reason = "missing/oversized product size"
        elif row.get("prvversion") is not None and type(row["prvversion"]) is not str:
            reason = "malformed documented processing version"
        evidence.append({"product": row, "exclusion_reason": reason})
        if reason is not None:
            continue
        product = {
            "tic_id": candidate["tic_id"],
            "sector": 1,
            "author": "SPOC",
            "exposure_seconds": 120,
            "observation_id": obs["obs_id"],
            "observation_group_id": str(obs["obsid"]),
            "product_group_id": str(row["obsID"]),
            "data_uri": product_uri(uri),
            "filename": name,
            "declared_bytes": row["size"],
            "archive_release_mjd": float(obs["t_obs_release"]),
            "archive_processing_version": row.get("prvversion"),
        }
        if uri in eligible and eligible[uri] != product:
            raise DataValidationError(
                "product identity: conflicting duplicate metadata; inspect archive before selection"
            )
        eligible[uri] = product
    if not eligible:
        raise DataValidationError(
            "product selection: no eligible LC product; preserve exclusion inventory"
        )

    def key(p: dict) -> tuple:
        v = p["archive_processing_version"]
        match = re.fullmatch(r"(?:spoc-)?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-._a-zA-Z0-9]*)?", v or "")
        version = tuple(int(x) for x in match.groups()) if match else (0, 0, 0)
        return (-p["archive_release_mjd"], tuple(-n for n in version), p["filename"])

    chosen = sorted(eligible.values(), key=key)[0]
    for entry in evidence:
        if entry["exclusion_reason"] is None:
            entry["exclusion_reason"] = (
                None
                if entry["product"]["dataURI"] == chosen["data_uri"]
                else "older archive release/version or filename tie-break"
            )
    return chosen, evidence


def cache_name(product: dict) -> str:
    return canonical_hash(product_uri(product["data_uri"])) + ".fits"


def cached_source(cache: Path, product: dict) -> bytes | None:
    file = safe_file(cache, cache_name(product))
    if not file.exists():
        return None
    try:
        with file.open("rb") as stream:
            content = stream.read(MAX_PRODUCT + 1)
    except OSError as error:
        raise DataValidationError(
            "source cache: unreadable; restore the exact pinned product"
        ) from error
    if len(content) != product["declared_bytes"] or source_sha256(content) != product.get(
        "source_sha256"
    ):
        raise DataValidationError(
            "source cache: corrupt/unverified; preserve it and restore the exact pinned checksum"
        )
    return content


def save_prepared(cache: Path, curve: PreparedCurve) -> None:
    # Only generate numeric snapshots here; never deserialize arbitrary NPZ/cache objects.
    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_days=curve.time_days,
        flux=curve.flux,
        flux_err=curve.flux_err,
        support_counts=curve.support_counts,
    )
    name = curve.content_hash + ".npz"
    target = safe_file(cache, name)
    if target.exists():
        try:
            if target.stat().st_size > 4 * 1024**2:
                raise ValueError("oversized")
            # Compare deterministic numeric-only generated bytes without loading cached content.
            if target.read_bytes() != buffer.getvalue():
                raise ValueError("mismatch")
        except (OSError, ValueError) as error:
            raise DataValidationError(
                "prepared cache: corrupt; preserve it and rebuild from pinned source explicitly"
            ) from error
    else:
        atomic_write(cache, name, buffer.getvalue())


def verify_pinned(
    product: dict, content: bytes, config: ProtocolConfig, cache: Path
) -> PreparedCurve:
    curve, audit = prepare_tess(content, tic_id=product["tic_id"], config=config)
    if (
        source_sha256(content) != product["source_sha256"]
        or curve.content_hash != product["qc"]["prepared_hash"]
    ):
        raise DataValidationError(
            "pinned identity: source/preparation hash mismatch; do not su"
            "bstitute or refresh products"
        )
    if canonical_hash(audit) != canonical_hash(product["qc"]):
        raise DataValidationError(
            "pinned QC: preparation metadata changed; restore locked envi"
            "ronment or document amendment"
        )
    save_prepared(cache, curve)
    return curve


def require_final_manifest(manifest: dict) -> None:
    """Future benchmark entry points must call this before any benchmark search."""
    if manifest.get("status") != "final" or manifest.get("support_validation") != "complete":
        raise DataValidationError(
            "manifest: provisional eligibility; complete T03 shared suppo"
            "rt validation before benchmark searches"
        )


def validate_manifest(manifest: dict, config: ProtocolConfig) -> None:
    """Validate pinned identities before any download or filesystem-cache access."""
    try:
        if (
            manifest["schema_version"] != 1
            or manifest["protocol_id"] != config.protocol_id
            or manifest["status"] not in ("provisional", "final")
            or manifest["config_hash"] != input_settings_hash(config)
            or type(manifest["products"]) is not list
            or len(manifest["products"]) != 8
            or type(manifest["downloads_total"]) is not int
            or not 0 <= manifest["downloads_total"] <= 30
        ):
            raise ValueError("manifest fields")
        products = manifest["products"]
        if len({p["tic_id"] for p in products}) != 8:
            raise ValueError("TIC uniqueness")
        roles = [p["role"] for p in products]
        if roles != ["sanity", "development", "development", *(["evaluation"] * 5)]:
            raise ValueError("roles")
        if products[0]["tic_id"] != config.sanity.tic_id:
            raise ValueError("sanity")
        if [int(p["tic_id"]) for p in products[1:]] != sorted(
            int(p["tic_id"]) for p in products[1:]
        ):
            raise ValueError("ordering")
        for p in products:
            product_uri(p["data_uri"])
            if (
                type(p["tic_id"]) is not str
                or not p["tic_id"].isascii()
                or not p["tic_id"].isdigit()
                or int(p["filename"].split("-")[2]) != int(p["tic_id"])
                or p["data_uri"] != "mast:TESS/product/" + p["filename"]
                or p["sector"] != 1
                or p["author"] != "SPOC"
                or p["exposure_seconds"] != 120
                or type(p["declared_bytes"]) is not int
                or not 0 < p["declared_bytes"] <= MAX_PRODUCT
                or p["byte_size"] != p["declared_bytes"]
                or not re.fullmatch(r"[0-9a-f]{64}", p["source_sha256"])
                or not re.fullmatch(r"[0-9a-f]{64}", p["qc"]["prepared_hash"])
            ):
                raise ValueError("product fields")
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as error:
        raise DataValidationError(
            "manifest: invalid pinned identity/settings; restore recorded"
            " manifest without reselection"
        ) from error


def fetch_pinned(manifest: dict, config: ProtocolConfig, cache: Path, archive: MastArchive) -> dict:
    """Never modify manifest, including final/frozen manifests; journal all failures separately."""
    validate_manifest(manifest, config)
    ledger = (
        read_json(cache, "pinned_downloads.json")
        if safe_file(cache, "pinned_downloads.json").exists()
        else {"downloads": 0, "events": []}
    )
    if type(ledger.get("downloads")) is not int or not 0 <= ledger["downloads"] <= 30:
        raise DataValidationError("download ledger: corrupt; restore original acquisition evidence")
    archive.budget.downloaded_before = manifest["downloads_total"] + ledger["downloads"]
    event_log = {"started_utc": utc_now(), "mode": "pinned", "status": "running", "products": []}
    write_json(cache, "last_fetch.json", event_log)
    try:
        for product in manifest["products"]:
            event = {"data_uri": product["data_uri"], "status": "checking"}
            event_log["products"].append(event)
            content = cached_source(cache, product)
            if content is None:
                if archive.offline:
                    raise DataValidationError(
                        "source cache: missing while offline; fetch the exact pinned product online"
                    )
                if archive.budget.downloaded_before + archive.budget.downloads >= 30:
                    raise DataValidationError(
                        "download cap: 30 reached; obtain amendment before further acquisition"
                    )
                event.update(status="download_started", acquired_utc=utc_now())
                write_json(cache, "last_fetch.json", event_log)
                ledger["downloads"] += 1
                ledger["events"].append({"data_uri": product["data_uri"], "utc": utc_now()})
                write_json(cache, "pinned_downloads.json", ledger)
                content = archive.download(product["data_uri"], product["declared_bytes"])
                if source_sha256(content) != product["source_sha256"]:
                    raise DataValidationError(
                        "reacquisition hash: upstream changed; preserve original iden"
                        "tity without substitution"
                    )
                # Verify before installing a missing cache file.
                verify_pinned(product, content, config, cache)
                atomic_write(cache, cache_name(product), content)
                event["status"] = "reacquired"
            else:
                verify_pinned(product, content, config, cache)
                event["status"] = "cache_verified"
        event_log["status"] = "complete"
    except DataValidationError as error:
        event_log.update(status="failed", reason=str(error))
        raise
    finally:
        event_log.update(
            ended_utc=utc_now(),
            downloads=archive.budget.downloads,
            bytes_received=archive.budget.bytes_received,
        )
        write_json(cache, "last_fetch.json", event_log)
    return {
        "status": manifest["status"],
        "mode": "pinned",
        "products": len(manifest["products"]),
        "downloads": archive.budget.downloads,
        "bytes_received": archive.budget.bytes_received,
    }


def fetch(
    config: ProtocolConfig, *, data_root: Path, cache_root: Path, archive: MastArchive
) -> dict:
    """Resume pinned state or make the first provisional manifest. Never finalize T03."""
    manifest_file = safe_file(data_root, "manifest.json")
    config_hash = input_settings_hash(config)
    if manifest_file.exists():
        manifest = read_json(data_root, "manifest.json")
        return fetch_pinned(manifest, config, cache_root, archive)
    if config.frozen:
        raise DataValidationError(
            "manifest: missing under frozen config; restore reviewed manifest, do not reselect"
        )
    journal_file = safe_file(data_root, "acquisition.json")
    if journal_file.exists():
        state = read_json(data_root, "acquisition.json")
        if state["config_hash"] != config_hash:
            raise DataValidationError(
                "acquisition settings: mismatch; restore original settings before resuming"
            )
    else:
        state = {
            "schema_version": 1,
            "status": "acquiring",
            "config_hash": config_hash,
            "started_utc": utc_now(),
            "downloads_total": 0,
            "attempts": [],
            "invocations": [],
        }
    archive.budget.downloaded_before = state["downloads_total"]
    state.setdefault("query", QUERY)
    invocation = {
        "started_utc": utc_now(),
        "status": "running",
        "invoke_method": "GET",
        "response_idle_seconds": 60,
        "request_deadline_seconds": 180,
    }
    state["invocations"].append(invocation)
    write_json(data_root, "acquisition.json", state)
    try:
        inventory_path = safe_file(data_root, "candidate_inventory.json")
        if inventory_path.exists():
            inventory = read_json(data_root, "candidate_inventory.json")
            if canonical_hash(inventory) != state.get("inventory_hash"):
                raise DataValidationError(
                    "inventory hash: mismatch; restore original candidate evidence"
                )
        else:
            inventory = candidate_inventory(
                archive.query("Mast.Caom.Filtered", QUERY), config.sanity.tic_id
            )
            state["inventory_hash"] = canonical_hash(inventory)
            write_json(data_root, "candidate_inventory.json", inventory)
            write_json(data_root, "acquisition.json", state)
        candidates = inventory["candidates"]
        sanity = next(c for c in candidates if c["tic_id"] == config.sanity.tic_id)
        queue = [sanity] + [c for c in candidates if c["tic_id"] != config.sanity.tic_id]
        for candidate in queue:
            accepted = [
                a
                for a in state["attempts"]
                if a["status"] == "basic_qc_pass" and a["tic_id"] != config.sanity.tic_id
            ]
            if len(accepted) == 7:
                break
            old = next((a for a in state["attempts"] if a["tic_id"] == candidate["tic_id"]), None)
            if old is not None and old["status"] in (
                "basic_qc_pass",
                "basic_qc_excluded",
                "metadata_excluded",
            ):
                continue
            attempt = old
            if attempt is None:
                attempt = {"tic_id": candidate["tic_id"], "status": "selecting", "events": []}
                state["attempts"].append(attempt)
                write_json(data_root, "acquisition.json", state)
            if "product" not in attempt:
                products = archive.query(
                    "Mast.Caom.Products",
                    {"obsid": ",".join(str(o["obsid"]) for o in candidate["observations"])},
                )
                attempt["product_inventory"] = products
                try:
                    product, evidence = choose_product(products, candidate)
                except DataValidationError as error:
                    attempt.update(status="metadata_excluded", reason=str(error))
                    write_json(data_root, "acquisition.json", state)
                    if candidate is sanity:
                        raise
                    continue
                attempt.update(product=product, selection_evidence=evidence, status="selected")
                write_json(data_root, "acquisition.json", state)
            if "product" not in attempt:
                raise DataValidationError(
                    "selection state: interrupted before pinned product; inspect "
                    "saved metadata before resuming"
                )
            product = attempt["product"]
            content = cached_source(cache_root, product)
            if content is None:
                # Persist the attempt BEFORE I/O. Interrupted downloads conservatively count.
                if state["downloads_total"] >= 30:
                    raise DataValidationError(
                        "download cap: 30 reached; request protocol amendment before more downloads"
                    )
                if archive.offline:
                    raise DataValidationError(
                        "source cache: missing while offline; run explicit fetch onli"
                        "ne for the saved identity"
                    )
                state["downloads_total"] += 1
                event = {"utc": utc_now(), "status": "started"}
                attempt["events"].append(event)
                write_json(data_root, "acquisition.json", state)
                try:
                    content = archive.download(product["data_uri"], product["declared_bytes"])
                    digest = source_sha256(content)
                    if product.get("source_sha256") not in (None, digest):
                        raise DataValidationError(
                            "pinned source hash: upstream changed; preserve failure and o"
                            "riginal identity"
                        )
                    product.update(
                        source_sha256=digest,
                        acquired_utc=product.get("acquired_utc", utc_now()),
                        byte_size=len(content),
                    )
                    # Record expected identity first; a crash before the cache write is recoverable.
                    write_json(data_root, "acquisition.json", state)
                    atomic_write(cache_root, cache_name(product), content)
                    event["status"] = "downloaded"
                except DataValidationError as error:
                    event.update(status="failed", reason=str(error))
                    raise
            try:
                curve, audit = prepare_tess(content, tic_id=candidate["tic_id"], config=config)
                product["qc"] = audit
                reasons = basic_qc_reasons(curve, audit, config)
                attempt.update(
                    status="basic_qc_excluded" if reasons else "basic_qc_pass", reasons=reasons
                )
            except DataValidationError as error:
                if isinstance(error, TessQCError):
                    product["qc"] = error.audit
                attempt.update(status="basic_qc_excluded", reasons=[str(error)])
                write_json(data_root, "acquisition.json", state)
                if candidate is sanity:
                    raise
                continue
            # A disk/cache failure is not a reason to reject a scientific candidate.
            save_prepared(cache_root, curve)
            write_json(data_root, "acquisition.json", state)
            if candidate is sanity and reasons:
                raise DataValidationError(
                    "sanity basic QC: failed; inspect evidence without substituting another TIC"
                )
        accepted = [
            a["product"]
            for a in state["attempts"]
            if a["status"] == "basic_qc_pass" and a["tic_id"] != config.sanity.tic_id
        ]
        sanity_products = [
            a["product"]
            for a in state["attempts"]
            if a["status"] == "basic_qc_pass" and a["tic_id"] == config.sanity.tic_id
        ]
        if len(accepted) != 7 or len(sanity_products) != 1:
            raise DataValidationError(
                "selection feasibility: fewer than seven provisional passes p"
                "lus sanity; preserve denominators and request amendment"
            )
        products = [{**sanity_products[0], "role": "sanity"}] + [
            {**p, "role": "development" if index < 2 else "evaluation"}
            for index, p in enumerate(accepted)
        ]
        # Also verify earlier passes when resuming an interrupted initial acquisition.
        for product in products:
            content = cached_source(cache_root, product)
            if content is None:
                raise DataValidationError(
                    "source cache: earlier pass missing; restore pinned bytes bef"
                    "ore manifest creation"
                )
            verify_pinned(product, content, config, cache_root)
        manifest = {
            "schema_version": 1,
            "protocol_id": config.protocol_id,
            "config_hash": config_hash,
            "status": "provisional",
            "support_validation": "pending T03",
            "roles_provisional": True,
            "created_utc": utc_now(),
            "inventory_hash": state["inventory_hash"],
            "downloads_total": state["downloads_total"],
            "products": products,
            "selection_rule": (
                "numeric TIC order; newest documented archive release/version"
                "; ascending filename tie-break"
            ),
            "attribution": [
                "https://archive.stsci.edu/missions-and-data/tess",
                "https://mast.stsci.edu/api/v0/",
            ],
        }
        write_json(data_root, "manifest.json", manifest)
        state["status"] = "provisional"
        invocation["status"] = "complete"
    except DataValidationError as error:
        state["status"] = "incomplete"
        invocation.update(status="failed", reason=str(error))
        raise
    finally:
        invocation.update(
            ended_utc=utc_now(),
            downloads=archive.budget.downloads,
            bytes_received=archive.budget.bytes_received,
        )
        write_json(data_root, "acquisition.json", state)
    return {
        "status": "provisional",
        "mode": "selection",
        "products": 8,
        "downloads": archive.budget.downloads,
        "bytes_received": archive.budget.bytes_received,
    }


def write_selection_csv(data_root: Path) -> None:
    """Controlled identifiers/statuses and numeric QC only; arbitrary archive text stays in JSON."""
    state = read_json(data_root, "acquisition.json")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        ["tic_id", "status", "input_rows", "retained_rows", "prepared_bins", "baseline_days"]
    )
    for attempt in state["attempts"]:
        qc = attempt.get("product", {}).get("qc", {})
        writer.writerow(
            [
                int(attempt["tic_id"]),
                attempt["status"],
                *[
                    qc.get(k)
                    for k in (
                        "input_rows",
                        "retained_rows",
                        "prepared_bins",
                        "source_baseline_days",
                    )
                ],
            ]
        )
    atomic_write(data_root, "selection.csv", buffer.getvalue().encode("utf-8"))
