"""Offline T03 eligibility and explicit provisional-to-final manifest promotion."""

import copy
from datetime import datetime
from pathlib import Path

import numpy as np

from transitstress import acquisition as acq
from transitstress import cohort
from transitstress.config import ProtocolConfig, eligibility_reference_hash
from transitstress.errors import DataValidationError
from transitstress.injection import family_specs
from transitstress.masking import make_observation_mask, mask_record
from transitstress.models import PreparedCurve
from transitstress.provenance import canonical_hash
from transitstress.support import window_layout

RECORD = "t03_eligibility.json"


def support_evidence(curve: PreparedCurve, config: ProtocolConfig) -> dict:
    """All masks and windows, including every failing original index; no injection."""
    masks = []
    reasons = []
    for name in config.masks.names:
        mask = make_observation_mask(curve, name, config.master_seed, config.masks)
        saved = mask_record(curve, mask)
        kept = np.flatnonzero(mask.keep)
        windows = []
        for width in config.detrending.window_days:
            if len(kept):
                layout = window_layout(
                    curve.time_days[kept], width, config.detrending.segment_gap_days
                )
                counts = layout.neighbor_counts
                failed = kept[counts < config.detrending.minimum_neighbors].tolist()
                minimum = int(counts.min())
                segments = int(layout.segment_ids[-1]) + 1
            else:
                failed, minimum, segments = [], 0, 0
            passed = minimum >= config.detrending.minimum_neighbors
            windows.append(
                {
                    "window_days": width,
                    "minimum_neighbors": minimum,
                    "segment_count": segments,
                    "failing_original_indices": failed,
                    "passed": passed,
                }
            )
            if not passed:
                reasons.append(
                    f"{name}/{width}d: minimum support {minimum} < "
                    f"{config.detrending.minimum_neighbors}"
                )
        saved["windows"] = windows
        masks.append(saved)
    return {
        "curve_id": curve.curve_id,
        "prepared_hash": curve.content_hash,
        "passed": not reasons,
        "reasons": reasons,
        "masks": masks,
    }


def enumerate_cases(
    manifest: dict, curves: dict[str, PreparedCurve], config: ProtocolConfig, split: str
) -> tuple[dict, ...]:
    """Enumerate IDs/specifications only; never execute injections or searches."""
    acq.require_final_manifest(manifest)
    acq.validate_manifest(manifest, config)
    if manifest.get("t03_settings_hash") != eligibility_reference_hash(config):
        raise DataValidationError(
            "case settings: differ from final eligibility; restore saved settings"
        )
    return _enumerate_cases(manifest, curves, config, split)


def _enumerate_cases(
    manifest: dict, curves: dict[str, PreparedCurve], config: ProtocolConfig, split: str
) -> tuple[dict, ...]:
    """Also used to verify proposed final counts before promoting the manifest."""
    if split not in ("development", "evaluation"):
        raise DataValidationError("split: unsupported; use development or evaluation")
    cases = []
    for product in manifest["products"]:
        if product["role"] != split:
            continue
        curve = curves.get(product["tic_id"])
        if (
            not isinstance(curve, PreparedCurve)
            or curve.content_hash != product["qc"]["prepared_hash"]
        ):
            raise DataValidationError("case curve: changed prepared identity; restore pinned data")
        for name in config.masks.names:
            mask = make_observation_mask(curve, name, config.master_seed, config.masks)
            for spec in family_specs(curve, config.injection):
                identity = {
                    "kind": "injection",
                    "curve_id": curve.curve_id,
                    "prepared_hash": curve.content_hash,
                    "mask_definition": name,
                    "mask_hash": mask.checksum,
                    "injection": spec,
                }
                cases.append({"case_id": canonical_hash(identity), **identity})
    expected = 54 if split == "development" else 135
    if len(cases) != expected or len({c["case_id"] for c in cases}) != expected:
        raise DataValidationError(
            "case enumeration: incomplete/duplicate split; restore final cohort"
        )
    return tuple(cases)


def _install_record(root: Path, name: str, value: dict) -> None:
    """Idempotent evidence installation, never silently refresh a previous identity."""
    if acq.safe_file(root, name).exists():
        if canonical_hash(acq.read_json(root, name)) != canonical_hash(value):
            raise DataValidationError(
                "T03 evidence: existing identity conflict; preserve and diagnose"
            )
    else:
        acq.write_json(root, name, value)


def finalize_eligibility(config: ProtocolConfig, *, data_root: Path, cache_root: Path) -> dict:
    """Reverify pinned sources, support and counts; persist evidence before final promotion.

    No live archive capability is accepted. A needed fresh reserve is a bounded
    authorization stop, never an implicit download or a substitute product.
    """
    if config.frozen:
        raise DataValidationError("T03: frozen configuration; do not alter eligibility")
    installed = acq.read_json(data_root, "manifest.json")
    acq.validate_manifest(installed, config)
    record_exists = acq.safe_file(data_root, RECORD).exists()
    previous = acq.read_json(data_root, RECORD) if record_exists else None
    if previous is not None:
        try:
            timestamp = datetime.fromisoformat(previous["verified_utc"])
            valid = (
                previous["schema_version"] == 1
                and previous["status"] == "complete"
                and timestamp.utcoffset() is not None
                and timestamp.utcoffset().total_seconds() == 0
                and type(previous["input_manifest"]) is dict
                and previous["input_manifest_hash"] == canonical_hash(previous["input_manifest"])
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise DataValidationError(
                "T03 evidence: malformed saved record; preserve and restore it"
            )
    if installed["status"] == "final":
        if previous is None:
            raise DataValidationError(
                "T03 final manifest: missing evidence; restore original record"
            )
        original = previous["input_manifest"]
    else:
        original = installed
    acq.validate_manifest(original, config)
    if original["status"] != "provisional" or original.get("roles_provisional") is not True:
        raise DataValidationError("T03 input: require preserved provisional manifest")
    schedule = cohort.load_cohort(config)
    state = cohort.initialize(config, data_root, schedule)
    if state["downloads_total"] != original["downloads_total"]:
        raise DataValidationError(
            "T03 accounting: journal/manifest conflict; preserve and diagnose"
        )
    ledger_path = acq.safe_file(cache_root, "pinned_downloads.json")
    ledger = acq.read_json(cache_root, ledger_path.name) if ledger_path.exists() else None
    if ledger is not None and ledger.get("downloads") != 0:
        raise DataValidationError(
            "T03 accounting: extra pinned reservations; reconcile before promotion"
        )
    products = {p["tic_id"]: p for p in original["products"]}
    accepted, audits, curves = [], [], {}
    sanity = None
    for pin in schedule["products"]:
        tic = pin["tic_id"]
        # Apply the complete support test to every original provisional product.
        # Only consult reserves when fewer than seven original candidates pass.
        if tic not in products and len(accepted) == 7:
            continue
        product = products.get(tic)
        if product is None:
            attempt = next((a for a in state["attempts"] if a["tic_id"] == tic), None)
            if attempt is not None and attempt["status"] == "basic_qc_excluded":
                audits.append(
                    {
                        "tic_id": tic,
                        "passed": False,
                        "reasons": attempt["reasons"],
                        "status": "basic_qc_excluded",
                    }
                )
                continue
            if attempt is None or attempt["status"] != "basic_qc_pass":
                blocked = {
                    "status": "reserve_authorization_required",
                    "next_tic": tic,
                    "next_data_uri": pin["data_uri"],
                    "maximum_product_bytes": pin["declared_bytes"],
                    "downloads_total": state["downloads_total"],
                    "audits": audits,
                    "input_manifest_hash": canonical_hash(original),
                }
                acq.write_json(data_root, "t03_blocked.json", blocked)
                raise DataValidationError(
                    f"T03 reserve {tic}: fresh source required; obtain bounded "
                    "authorization for the next exact scheduled product"
                )
            product = attempt["product"]
        cohort.match_pin(product, pin)
        content = acq.cached_source(cache_root, product)
        if content is None:
            raise DataValidationError(
                "T03 source cache: missing pinned source; restore exact bytes"
            )
        curve = acq.verify_pinned(product, content, config, cache_root)
        basic_reasons = acq.basic_qc_reasons(curve, product["qc"], config)
        audit = {"tic_id": tic, **support_evidence(curve, config)}
        audit["reasons"] = basic_reasons + audit["reasons"]
        audit["passed"] = not audit["reasons"]
        audit["status"] = "eligible" if audit["passed"] else "support_excluded"
        audits.append(audit)
        curves[tic] = curve
        if pin["role"] == "sanity":
            if not audit["passed"]:
                acq.write_json(
                    data_root, "t03_blocked.json", {"status": "sanity_failed", "audits": audits}
                )
                raise DataValidationError(
                    "T03 sanity: support failed; preserve exclusion and diagnose"
                )
            sanity = product
        elif audit["passed"]:
            accepted.append(product)
    if len(accepted) != 7 or sanity is None:
        acq.write_json(
            data_root, "t03_blocked.json", {"status": "finite_pool_exhausted", "audits": audits}
        )
        raise DataValidationError("T03 finite-pool exhaustion: obtain explicit cohort amendment")
    final = copy.deepcopy(original)
    final.update(status="final", support_validation="complete", roles_provisional=False)
    final["products"] = [{**sanity, "role": "sanity"}] + [
        {**p, "role": "development" if i < 2 else "evaluation"} for i, p in enumerate(accepted)
    ]
    counts = {
        split: len(_enumerate_cases(final, curves, config, split))
        for split in ("development", "evaluation")
    }
    evidence = {
        "schema_version": 1,
        "status": "complete",
        "verified_utc": previous["verified_utc"] if previous else acq.utc_now(),
        "input_manifest": original,
        "input_manifest_hash": canonical_hash(original),
        "settings_hash": canonical_hash(config),
        "cohort_journal_hash": canonical_hash(state),
        "downloads_total": state["downloads_total"],
        "audits": audits,
        "injection_case_counts": counts,
        "searches_executed": 0,
        "unattempted_reserves": [
            p["tic_id"]
            for p in schedule["products"]
            if p["tic_id"] not in curves and all(a["tic_id"] != p["tic_id"] for a in audits)
        ],
    }
    final["t03_evidence_hash"] = canonical_hash(evidence)
    final["t03_settings_hash"] = evidence["settings_hash"]
    final["finalized_utc"] = evidence["verified_utc"]
    final["selection_record"] = [
        {"tic_id": a["tic_id"], "status": a["status"], "reasons": a["reasons"]} for a in audits
    ]
    acq.validate_manifest(final, config)
    # Validate every byte before starting promotion, including repeat/final conflicts.
    if installed["status"] == "final" and canonical_hash(installed) != canonical_hash(final):
        raise DataValidationError("T03 final manifest: identity conflict; preserve and diagnose")
    _install_record(data_root, RECORD, evidence)
    if installed["status"] == "provisional":
        acq.write_json(data_root, "manifest.json", final)
    return {
        "status": "final",
        "products": len(final["products"]),
        "injection_case_counts": counts,
        "evidence_hash": final["t03_evidence_hash"],
        "downloads_total": state["downloads_total"],
        "new_downloads": 0,
    }
