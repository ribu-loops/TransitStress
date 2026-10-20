"""Offline known-signal diagnostic; literature metadata enters only after search."""

import io
from dataclasses import asdict
from pathlib import Path

import numpy as np

from transitstress import acquisition as acq
from transitstress.config import ProtocolConfig
from transitstress.detrending import detrend
from transitstress.eligibility import support_evidence
from transitstress.errors import DataValidationError
from transitstress.models import DetrendConfig, PreparedCurve
from transitstress.provenance import canonical_hash
from transitstress.search import BLSOutput, build_search_grid, search_bls

PIPELINES = (DetrendConfig("rm_0p5d", 0.5), DetrendConfig("rm_1p5d", 1.5))


def blind_pair(curve: PreparedCurve, config: ProtocolConfig) -> tuple[dict, dict, dict]:
    """Build one original grid and independently fit both trends, with no truth input."""
    grid = build_search_grid(curve, config.search)
    summaries, arrays, diagnostics = {}, {"period_days": grid.periods_days}, {}
    arrays.update(time_days=curve.time_days, flux=curve.flux, flux_err=curve.flux_err)
    for pipeline in PIPELINES:
        processed = detrend(curve.time_days, curve.flux, curve.flux_err, pipeline)
        output: BLSOutput = search_bls(
            curve.time_days, processed.flux, processed.flux_err, grid, config.search
        )
        name = pipeline.pipeline_id
        summaries[name] = asdict(output.result)
        diagnostics[name] = {
            "minimum_neighbors": int(processed.neighbor_counts.min()),
            "segment_count": int(processed.segment_ids.max()) + 1,
            "nonfinite_periodogram_counts": {
                key: int(np.count_nonzero(~np.isfinite(values)))
                for key, values in output.periodogram.items()
            },
        }
        arrays.update(
            {
                f"{name}_{key}": values
                for key, values in output.periodogram.items()
                if key != "period_days"
            }
        )
        for key in ("flux", "flux_err", "trend", "neighbor_counts", "segment_ids"):
            arrays[f"{name}_{key}"] = getattr(processed, key)
    return (
        {
            "length": len(grid.periods_days),
            "hash": grid.checksum,
            "search_config_hash": grid.search_config_hash,
            "original_baseline_days": grid.original_baseline_days,
            "first_period_days": float(grid.periods_days[0]),
            "last_period_days": float(grid.periods_days[-1]),
            "settings": asdict(config.search),
        },
        {"searches": summaries, "support": diagnostics},
        arrays,
    )


def run_sanity(
    config: ProtocolConfig,
    *,
    manifest_path: Path,
    cache_root: Path,
    output_root: Path,
) -> dict:
    """Use exact cached sanity bytes only; no acquisition/fallback capability exists."""
    acq.safe_file(output_root, "sanity.json")
    acq.safe_file(output_root, "sanity_arrays.npz")
    manifest = acq.read_json(manifest_path.parent, manifest_path.name)
    acq.validate_manifest(manifest, config)
    acq.require_final_manifest(manifest)
    evidence = acq.read_json(manifest_path.parent, "t03_eligibility.json")
    if canonical_hash(evidence) != manifest["t03_evidence_hash"] or evidence.get(
        "settings_hash"
    ) != canonical_hash(config):
        raise DataValidationError("sanity eligibility: changed evidence/settings; restore T03 pins")
    product = next(p for p in manifest["products"] if p["role"] == "sanity")
    content = acq.cached_source(cache_root, product)
    if content is None:
        raise DataValidationError(
            "sanity source: missing exact cache; restore pinned bytes offline"
        )
    curve = acq.verify_pinned(product, content, config, cache_root)
    saved = next(a for a in evidence["audits"] if a["tic_id"] == product["tic_id"])
    actual = support_evidence(curve, config)
    if any(saved.get(key) != value for key, value in actual.items()):
        raise DataValidationError(
            "sanity support: saved masks/indices differ; preserve and diagnose"
        )
    # Both searches must finish before even opening the reference file.
    grid, outputs, arrays = blind_pair(curve, config)
    reference = acq.read_json(manifest_path.parent, "sanity_reference.json")
    try:
        valid = (
            reference["tic_id"] == product["tic_id"]
            and reference["time_scale"] == curve.time_metadata.time_scale == "tdb"
            and reference["time_format"] == "jd"
            and curve.time_metadata.absolute_epoch is not None
            and type(reference["period_days"]) in (int, float)
            and np.isfinite(reference["period_days"])
            and reference["period_days"] > 0
            and type(reference["epoch_bjd_tdb"]) in (int, float)
            and np.isfinite(reference["epoch_bjd_tdb"])
        )
    except KeyError:
        valid = False
    if not valid:
        raise DataValidationError(
            "sanity reference: invalid ephemeris/units; restore cited metadata"
        )
    comparisons = {}
    for name, result in outputs["searches"].items():
        if result["status"] != "ok":
            comparisons[name] = {"within_one_percent": None, "reason": result["failure_reason"]}
            continue
        period = result["period_days"]
        relative_error = abs(period - reference["period_days"]) / reference["period_days"]
        absolute_epoch = curve.time_metadata.absolute_epoch + result["epoch_days"]
        epoch_difference = absolute_epoch - reference["epoch_bjd_tdb"]
        phase_offset = (epoch_difference + reference["period_days"] / 2) % reference[
            "period_days"
        ] - reference["period_days"] / 2
        comparisons[name] = {
            "relative_period_error": relative_error,
            "within_one_percent": relative_error <= 0.01,
            "fitted_epoch_bjd_tdb": absolute_epoch,
            "nearest_reference_epoch_offset_days": phase_offset,
            "reason": None,
        }
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    numerical = buffer.getvalue()
    if len(numerical) > 8 * 1024**2:
        raise DataValidationError("sanity artifact: over 8 MiB; preserve bounded output")
    report = {
        "schema_version": 1,
        "kind": "real_known_signal_sanity_not_benchmark",
        "verified_utc": acq.utc_now(),
        "status": "error"
        if any(r["status"] != "ok" for r in outputs["searches"].values())
        else "complete",
        "tic_id": product["tic_id"],
        "product": product,
        "prepared_hash": curve.content_hash,
        "time_metadata": asdict(curve.time_metadata),
        "original_start_days": curve.original_start_days,
        "original_end_days": curve.original_end_days,
        "manifest_hash": canonical_hash(manifest),
        "eligibility_hash": canonical_hash(evidence),
        "config_hash": canonical_hash(config),
        "reference": reference,
        "grid": grid,
        **outputs,
        "post_search_comparison": comparisons,
        "numerical_file": "sanity_arrays.npz",
        "numerical_file_sha256": acq.source_sha256(numerical),
        "limitations": [
            "Separate known-signal sanity check; not a discovery, confirmation or benchmark.",
            "BLS power and depth SNR are diagnostics, not calibrated significance.",
            "Trend uncertainty and induced correlations are omitted from propagated errors.",
            "Original observation mask only; benchmark/evaluation searches not executed.",
        ],
    }
    acq.atomic_write(output_root, "sanity_arrays.npz", numerical)
    acq.write_json(output_root, "sanity.json", report)
    return report
