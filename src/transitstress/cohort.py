"""Fixed exact-product cohort; no sector discovery, truth or search inputs."""

import copy
import json
from pathlib import Path

from transitstress import acquisition as acq
from transitstress.archive import MAX_METADATA, MastArchive, product_uri, source_sha256
from transitstress.config import ProtocolConfig
from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash
from transitstress.tess_input import TessQCError

SCHEDULE = Path(__file__).resolve().parents[2] / "configs/t02_cohort_v1.json"
JOURNAL = "cohort_acquisition.json"
BASIS = "exact scheduled LC identity; no release comparison"
LEGACY_HASHES = {
    "acquisition.json": "3ff162927ff50790030596c83590ef59b9ea68e2541291c65be2921a94a56dbc",
    "candidate_inventory.json": "153c29e7524e38c7518f66aa83da433e5c9caf245f75e300e4a4f16c9b38b502",
    "candidate_inventory_rejected_paging.json": (
        "153c29e7524e38c7518f66aa83da433e5c9caf245f75e300e4a4f16c9b38b502"
    ),
}
PIN_KEYS = (
    "tic_id",
    "observation_group_id",
    "observation_id",
    "product_group_id",
    "filename",
    "data_uri",
    "author",
    "sector",
    "exposure_seconds",
    "declared_bytes",
    "archive_processing_version",
    "archive_release_mjd",
)


def load_cohort(config: ProtocolConfig) -> dict:
    """Read one bounded developer-owned record; config pins its complete content."""
    try:
        with SCHEDULE.open("rb") as stream:
            content = stream.read(MAX_METADATA + 1)
        record = json.loads(content)
        if len(content) > MAX_METADATA or canonical_hash(record) != config.selection.cohort_sha256:
            raise ValueError("hash")
        entries = record["products"]
        if (
            record["schema_version"] != 1
            or record["cohort_id"] != config.selection.cohort_id
            or len(entries) != 10
            or len({p["tic_id"] for p in entries}) != 10
            or entries[0]["tic_id"] != config.sanity.tic_id
            or [p["order"] for p in entries] != list(range(10))
            or [p["role"] for p in entries] != ["sanity"] + ["candidate"] * 9
            or [int(p["tic_id"]) for p in entries[1:]]
            != sorted(int(p["tic_id"]) for p in entries[1:])
        ):
            raise ValueError("schedule")
        for p in entries:
            product_uri(p["data_uri"])
            if p["data_uri"] != "mast:TESS/product/" + p["filename"]:
                raise ValueError("URI")
        return record
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        raise DataValidationError(
            "cohort identity: missing/changed/invalid; restore the approved exact schedule"
        ) from error


def match_pin(product: dict, pin: dict) -> None:
    """Identity disagreement is never an exclusion or permission to choose another file."""
    if any(product.get(k) != pin[k] for k in PIN_KEYS):
        raise DataValidationError(
            "cohort pin: identity/metadata conflict; preserve evidence and stop"
        )


def _check_uri_metadata(rows: list[dict], pins_by_uri: dict[str, dict]) -> None:
    """A row asserting a scheduled URI must agree even when individually ineligible."""
    for row in rows:
        pin = pins_by_uri.get(row.get("dataURI"))
        if pin is None:
            continue
        expected = {
            "productFilename": pin["filename"],
            "obs_id": pin["observation_id"],
            "obs_collection": "TESS",
            "dataRights": "PUBLIC",
            "productType": "SCIENCE",
            "size": pin["declared_bytes"],
            "prvversion": pin["archive_processing_version"],
        }
        if (
            any(row.get(k) != v for k, v in expected.items())
            or type(row.get("size")) is not int
            or str(row.get("obsID")) != pin["product_group_id"]
            or str(row.get("parent_obsid")) != pin["observation_group_id"]
        ):
            raise DataValidationError(
                "cohort metadata identity conflict: scheduled URI row disagrees; preserve evidence"
            )


def _history_attempts(history: dict) -> list[dict]:
    return history["attempts"] + [
        a for rejection in history["rejected_inventories"] for a in rejection["attempts"]
    ]


def _check_recorded_metadata(attempts: list[dict], record: dict) -> None:
    """Check raw historical/current rows and normalized pins without choosing a winner."""
    pins = {p["data_uri"]: p for p in record["products"]}
    seen = {}
    for attempt in attempts:
        product = attempt.get("product")
        if product is not None and product.get("data_uri") in pins:
            uri = product["data_uri"]
            match_pin(product, pins[uri])
            if attempt["tic_id"] != pins[uri]["tic_id"]:
                raise DataValidationError(
                    "cohort recorded identity conflict: URI/TIC disagree; stop"
                )
            known = seen.setdefault(uri, {})
            for key in ("source_sha256", "byte_size"):
                value = product.get(key)
                if value is None:
                    continue  # An incomplete attempt cannot erase an earlier known pin.
                if key in known and known[key] != value:
                    raise DataValidationError(
                        "cohort recorded identity conflict: same-URI source pins; stop"
                    )
                known[key] = value
        rows = attempt.get("product_inventory", []) + [
            entry["product"] for entry in attempt.get("selection_evidence", [])
        ]
        _check_uri_metadata(rows, pins)


def _legacy(data_root: Path) -> dict | None:
    if not acq.safe_file(data_root, "acquisition.json").exists():
        if any(
            acq.safe_file(data_root, n).exists() for n in LEGACY_HASHES if n != "acquisition.json"
        ):
            raise DataValidationError("migration: orphaned rejection evidence; restore journal")
        return None
    for name, expected in LEGACY_HASHES.items():
        # read_json enforces size, containment and strict finite JSON before hashing.
        acq.read_json(data_root, name)
        with acq.safe_file(data_root, name).open("rb") as stream:
            content = stream.read(MAX_METADATA + 1)
        if len(content) > MAX_METADATA or source_sha256(content) != expected:
            raise DataValidationError(
                "migration: historical evidence identity conflict; restore original evidence"
            )
    history = acq.read_json(data_root, "acquisition.json")
    if history["downloads_total"] != 5:
        raise DataValidationError("migration: expected five spent reservations; stop")
    return history


def initialize(config: ProtocolConfig, data_root: Path, record: dict) -> dict:
    """One atomic self-contained migration; historical files are never modified."""
    history = _legacy(data_root)
    if history:
        _check_recorded_metadata(_history_attempts(history), record)
    if acq.safe_file(data_root, JOURNAL).exists():
        state = acq.read_json(data_root, JOURNAL)
        if (
            state.get("schema_version") != 2
            or state.get("config_hash") != acq.input_settings_hash(config)
            or state.get("cohort_hash") != canonical_hash(record)
            or state.get("history") != history
            or state.get("legacy_hashes") != (LEGACY_HASHES if history else {})
            or type(state.get("downloads_total")) is not int
            or not (5 if history else 0) <= state["downloads_total"] <= 30
            or state["downloads_total"]
            != (5 if history else 0) + len(state.get("reservations", []))
            or state.get("cohort") != record
        ):
            raise DataValidationError("cohort resume: settings/history/accounting conflict; stop")
        try:
            _validate_attempts(state, record)
        except (KeyError, TypeError, AttributeError) as error:
            raise DataValidationError(
                "cohort journal: malformed evidence; restore recorded file"
            ) from error
        return state
    pins = {p["tic_id"]: p for p in record["products"]}
    inherited = []
    if history:
        old_attempts = _history_attempts(history)
        for old in old_attempts:
            product = old.get("product")
            if product is None:
                continue  # Still retained in the complete immutable history snapshot.
            if old["tic_id"] not in pins:
                raise DataValidationError("migration pin: unscheduled historical product; stop")
            match_pin(product, pins[old["tic_id"]])
            if any(a["tic_id"] == old["tic_id"] for a in inherited):
                raise DataValidationError("migration pin: duplicate historical pin; stop")
            attempt = copy.deepcopy(old)
            attempt["inherited_attempt"] = copy.deepcopy(old)
            inherited.append(attempt)
    state = {
        "schema_version": 2,
        "protocol_id": config.protocol_id,
        "config_hash": acq.input_settings_hash(config),
        "cohort_hash": canonical_hash(record),
        "cohort": record,
        "history": history,
        "legacy_hashes": dict(LEGACY_HASHES) if history else {},
        "started_utc": acq.utc_now(),
        "status": "acquiring",
        "downloads_total": 5 if history else 0,
        "attempts": inherited,
        "invocations": [],
        "metadata": {},
        "reservations": [],
        "metadata_attempts": [],
    }
    acq.write_json(data_root, JOURNAL, state)
    return state


def _validate_attempts(state: dict, record: dict) -> None:
    pins = {p["tic_id"]: p for p in record["products"]}
    _check_recorded_metadata(state["attempts"], record)
    seen = set()
    for attempt in state["attempts"]:
        tic = attempt["tic_id"]
        if tic not in pins or tic in seen:
            raise DataValidationError("cohort attempts: duplicate/unscheduled TIC; stop")
        seen.add(tic)
        match_pin(attempt["product"], pins[tic])
    if state["history"]:
        for rejection in state["history"]["rejected_inventories"]:
            for old in rejection["attempts"]:
                previous = old.get("product", {})
                if not previous.get("source_sha256"):
                    continue
                current = next((a for a in state["attempts"] if a["tic_id"] == old["tic_id"]), None)
                if current is None or any(
                    current["product"].get(k) != previous[k]
                    for k in ("source_sha256", "byte_size", "acquired_utc")
                ):
                    raise DataValidationError("cohort inherited source pin: changed/missing; stop")
    for tic, verified in state["metadata"].items():
        if tic not in pins:
            raise DataValidationError("cohort metadata: unscheduled identity; stop")
        match_pin(verified["product"], pins[tic])
        if verified.get("evidence_hash") != canonical_hash(verified["evidence"]):
            raise DataValidationError("cohort metadata: evidence hash conflict; stop")
        _check_uri_metadata(verified["evidence"]["products"], {pins[tic]["data_uri"]: pins[tic]})


def verify_metadata(pin: dict, archive: MastArchive, evidence: dict) -> dict:
    """Verify one scheduled identity, retaining unrelated metadata without selecting it."""
    params = {
        **acq.QUERY,
        "filters": acq.QUERY["filters"] + [{"paramName": "target_name", "values": [pin["tic_id"]]}],
    }
    evidence["query"] = params
    observations = archive.query("Mast.Caom.Filtered", params)
    evidence["observations"] = observations
    inventory = acq.candidate_inventory(observations, pin["tic_id"])
    if any(c["tic_id"] != pin["tic_id"] for c in inventory["candidates"]):
        raise DataValidationError("cohort observation: target predicate conflict; stop")
    selected = [o for o in observations if str(o["obsid"]) == pin["observation_group_id"]]
    if len(selected) != 1 or selected[0]["obs_id"] != pin["observation_id"]:
        raise DataValidationError("cohort observation: exact scheduled identity missing; stop")
    products = archive.query("Mast.Caom.Products", {"obsid": pin["observation_group_id"]})
    evidence["products"] = products
    _check_uri_metadata(products, {pin["data_uri"]: pin})
    exact = [p for p in products if p.get("dataURI") == pin["data_uri"]]
    product, _ = acq.choose_product(exact, {"tic_id": pin["tic_id"], "observations": selected})
    match_pin(product, pin)
    product["selection_basis"] = BASIS
    return {"product": product, "evidence": evidence, "evidence_hash": canonical_hash(evidence)}


def validate_manifest_cohort(manifest: dict, config: ProtocolConfig) -> None:
    record = load_cohort(config)
    if manifest.get("cohort_hash") != canonical_hash(record):
        raise DataValidationError("manifest cohort: changed schedule identity; stop")
    pins = {p["tic_id"]: p for p in record["products"]}
    for p in manifest["products"]:
        if p["tic_id"] not in pins:
            raise DataValidationError("manifest cohort: unscheduled product; stop")
        match_pin(p, pins[p["tic_id"]])


def _reconcile_source(attempt: dict, audit: dict) -> None:
    """Provenance must succeed before any QC exclusion may advance selection."""
    product = attempt["product"]
    product["qc"] = audit
    attempt["status"] = "processing_unresolved"
    try:
        product["processing_reconciliation"] = acq.reconcile_processing(product, audit)
    except (KeyError, TypeError) as error:
        raise DataValidationError(
            "processing identity: incomplete/invalid audit; resolve provenance before QC exclusion"
        ) from error


def _source(
    attempt: dict,
    state: dict,
    config: ProtocolConfig,
    data: Path,
    cache: Path,
    archive: MastArchive,
    recheck: bool,
) -> None:
    product = attempt["product"]
    if attempt["status"] == "basic_qc_excluded" and not recheck:
        attempt.setdefault("qc_rejection_reasons", list(attempt.get("reasons", [])))
        _reconcile_source(attempt, product.get("qc", {}))
        attempt["status"] = "basic_qc_excluded"
        return
    if attempt["status"] == "basic_qc_pass":
        content = acq.cached_source(cache, product)
        if content is None:
            raise DataValidationError("cohort cache: earlier pass missing; restore exact source")
        acq.verify_pinned(product, content, config, cache)
        return
    content = acq.cached_source(cache, product)
    if recheck and attempt["status"] in ("basic_qc_excluded", "processing_unresolved"):
        if content is None:
            raise DataValidationError("QC recheck: exact cached source missing; restore it first")
        attempt.setdefault("qc_history", []).append(
            {
                "status": attempt["status"],
                "reasons": attempt.get("reasons", []),
                "qc": product.get("qc"),
                "rechecked_utc": acq.utc_now(),
            }
        )
    if content is None:
        if archive.offline:
            raise DataValidationError("cohort cache: missing offline; restore exact source")
        if state["downloads_total"] >= 30:
            raise DataValidationError("download cap: 30 spent; obtain explicit amendment")
        state["downloads_total"] += 1
        state["reservations"].append({"tic_id": product["tic_id"], "utc": acq.utc_now()})
        event = {"status": "started", "utc": acq.utc_now()}
        attempt["events"].append(event)
        acq.write_json(data, JOURNAL, state)  # Reservation survives interruption before I/O.
        try:
            content = archive.download(product["data_uri"], product["declared_bytes"])
            if len(content) != product["declared_bytes"]:
                raise DataValidationError("cohort source: declared size conflict; stop")
            digest = source_sha256(content)
            if product.get("source_sha256") not in (None, digest):
                raise DataValidationError("cohort source: pinned hash changed; stop")
            product.update(
                source_sha256=digest,
                byte_size=len(content),
                acquired_utc=product.get("acquired_utc", acq.utc_now()),
            )
            acq.write_json(data, JOURNAL, state)  # Identity before cache installation.
            acq.atomic_write(cache, acq.cache_name(product), content)
            event["status"] = "downloaded"
        except DataValidationError as error:
            event.update(status="failed", reason=str(error))
            raise
    try:
        curve, audit = acq.prepare_tess(content, tic_id=product["tic_id"], config=config)
    except TessQCError as error:
        attempt["qc_rejection_reasons"] = [str(error)]
        _reconcile_source(attempt, error.audit)
        attempt.update(status="basic_qc_excluded", reasons=[str(error)])
        return
    # Metadata/unit/identity parsing errors and processing disagreement stop, not exclude.
    _reconcile_source(attempt, audit)
    reasons = acq.basic_qc_reasons(curve, audit, config)
    attempt.update(status="basic_qc_excluded" if reasons else "basic_qc_pass", reasons=reasons)
    acq.save_prepared(cache, curve)


def fetch_cohort(
    config: ProtocolConfig,
    *,
    data_root: Path,
    cache_root: Path,
    archive: MastArchive,
    recheck_qc: bool = False,
    metadata_only: bool = False,
) -> dict:
    """Exact metadata first, then bounded sources; never finalize T03 support."""
    record = load_cohort(config)
    if acq.safe_file(data_root, "manifest.json").exists():
        if recheck_qc or metadata_only:
            raise DataValidationError("manifest already exists: refuse reselection/recheck")
        manifest = acq.read_json(data_root, "manifest.json")
        validate_manifest_cohort(manifest, config)
        if acq.safe_file(data_root, JOURNAL).exists():
            saved = initialize(config, data_root, record)
            if saved["status"] != "provisional" and saved.get("pending_manifest"):
                if canonical_hash(manifest) != saved.get("pending_manifest_hash"):
                    raise DataValidationError(
                        "manifest promotion: installed identity conflict; stop"
                    )
        result = acq.fetch_pinned(manifest, config, cache_root, archive)
        if acq.safe_file(data_root, JOURNAL).exists() and saved["status"] != "provisional":
            saved["status"] = "provisional"
            for old in saved["invocations"]:
                if old["status"] == "running":
                    old.update(status="interrupted", observed_utc=acq.utc_now())
            acq.write_json(data_root, JOURNAL, saved)
        return result
    if config.frozen:
        raise DataValidationError("manifest: absent under frozen settings; do not select")
    state = initialize(config, data_root, record)
    archive.budget.downloaded_before = state["downloads_total"]
    for old in state["invocations"]:
        if old["status"] == "running":
            old.update(
                status="interrupted",
                observed_utc=acq.utc_now(),
                reason="prior invocation did not flush final accounting",
            )
    invocation = {
        "started_utc": acq.utc_now(),
        "status": "running",
        "metadata_only": metadata_only,
        "downloads": None,
        "bytes_received": None,
    }
    state["invocations"].append(invocation)
    acq.write_json(data_root, JOURNAL, state)
    try:
        if state.get("pending_manifest"):
            if metadata_only or recheck_qc:
                raise DataValidationError(
                    "manifest pending: resume exact promotion without recheck"
                )
            manifest = state["pending_manifest"]
            if canonical_hash(manifest) != state.get("pending_manifest_hash"):
                raise DataValidationError("manifest promotion: pending identity conflict; stop")
            acq.validate_manifest(manifest, config)
            for product in manifest["products"]:
                content = acq.cached_source(cache_root, product)
                if content is None:
                    raise DataValidationError("manifest promotion: exact cache missing; restore it")
                acq.verify_pinned(product, content, config, cache_root)
            acq.write_json(data_root, "manifest.json", manifest)
            state["status"] = "provisional"
            invocation["status"] = "complete"
            return {"status": "provisional", "mode": "promotion", "downloads": 0}
        for pin in record["products"]:
            tic = pin["tic_id"]
            if tic not in state["metadata"]:
                probe = {"tic_id": tic, "status": "running", "evidence": {}}
                state["metadata_attempts"].append(probe)
                acq.write_json(data_root, JOURNAL, state)
                try:
                    verified = verify_metadata(pin, archive, probe["evidence"])
                except DataValidationError as error:
                    probe.update(status="failed", reason=str(error))
                    raise
                probe["status"] = "complete"
                state["metadata"][tic] = verified
                acq.write_json(data_root, JOURNAL, state)
        if metadata_only:
            state["status"] = "cohort_metadata_verified"
            invocation["status"] = "complete"
            return {
                "status": state["status"],
                "mode": "cohort_metadata",
                "downloads": 0,
                "cohort_hash": state["cohort_hash"],
            }
        accepted = []
        sanity = None
        for pin in record["products"]:
            if len(accepted) == 7:
                break
            tic = pin["tic_id"]
            attempt = next((a for a in state["attempts"] if a["tic_id"] == tic), None)
            if attempt is None:
                attempt = {
                    "tic_id": tic,
                    "product": copy.deepcopy(state["metadata"][tic]["product"]),
                    "status": "selected",
                    "events": [],
                }
                state["attempts"].append(attempt)
            match_pin(attempt["product"], pin)
            acq.write_json(data_root, JOURNAL, state)
            _source(attempt, state, config, data_root, cache_root, archive, recheck_qc)
            acq.write_json(data_root, JOURNAL, state)
            if pin["role"] == "sanity":
                if attempt["status"] != "basic_qc_pass":
                    raise DataValidationError("sanity QC: failed; preserve source and stop")
                sanity = attempt["product"]
            elif attempt["status"] == "basic_qc_pass":
                accepted.append(attempt["product"])
        if len(accepted) != 7 or sanity is None:
            raise DataValidationError("cohort finite-pool exhaustion: need seven; obtain amendment")
        manifest = {
            "schema_version": 1,
            "protocol_id": config.protocol_id,
            "config_hash": acq.input_settings_hash(config),
            "cohort_hash": state["cohort_hash"],
            "status": "provisional",
            "support_validation": "pending T03",
            "roles_provisional": True,
            "created_utc": acq.utc_now(),
            "downloads_total": state["downloads_total"],
            "selection_rule": record["selection_rule"],
            "selection_limitation": record["limitation"],
            "legacy_hashes": state["legacy_hashes"],
            "metadata_evidence_hash": canonical_hash(state["metadata"]),
            "selection_record": [
                {"tic_id": a["tic_id"], "status": a["status"], "reasons": a.get("reasons", [])}
                for pin in record["products"]
                for a in state["attempts"]
                if a["tic_id"] == pin["tic_id"]
            ],
            "products": [{**sanity, "role": "sanity"}]
            + [
                {**p, "role": "development" if i < 2 else "evaluation"}
                for i, p in enumerate(accepted)
            ],
        }
        acq.validate_manifest(manifest, config)
        state["pending_manifest"] = manifest
        state["pending_manifest_hash"] = canonical_hash(manifest)
        state["status"] = "manifest_ready"
        acq.write_json(data_root, JOURNAL, state)
        acq.write_json(data_root, "manifest.json", manifest)
        state["status"] = "provisional"
        invocation["status"] = "complete"
        return {
            "status": "provisional",
            "mode": "cohort",
            "products": 8,
            "downloads": archive.budget.downloads,
            "bytes_received": archive.budget.bytes_received,
        }
    except DataValidationError as error:
        state["status"] = "incomplete"
        invocation.update(status="failed", reason=str(error))
        raise
    except (KeyboardInterrupt, SystemExit):
        invocation.update(status="interrupted", reason="explicit process interruption")
        for probe in state["metadata_attempts"]:
            if probe["status"] == "running":
                probe["status"] = "interrupted"
        raise
    finally:
        invocation.update(
            ended_utc=acq.utc_now(),
            downloads=archive.budget.downloads,
            bytes_received=archive.budget.bytes_received,
            transport=archive.diagnostics,
        )
        acq.write_json(data_root, JOURNAL, state)
