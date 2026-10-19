"""Local SPOC FITS validation and basic QC. No network or support/segmentation logic."""

import io
from dataclasses import asdict
from importlib.metadata import version

import astropy.units as u
import numpy as np
from astropy.io import fits
from astropy.time import Time
from lightkurve.utils import TessQualityFlags

from transitstress.archive import source_sha256
from transitstress.config import ProtocolConfig
from transitstress.errors import DataValidationError
from transitstress.models import PreparedCurve, TimeMetadata
from transitstress.preparation import prepare_curve


class TessQCError(DataValidationError):
    """Rejected preparation with all available raw quality counts retained for audit."""

    def __init__(self, message: str, audit: dict):
        super().__init__(message)
        self.audit = audit


def prepare_tess(
    content: bytes, *, tic_id: str, config: ProtocolConfig
) -> tuple[PreparedCurve, dict]:
    """Use unfiltered FITS rows for audit and Lightkurve's resolved default mask.

    BTJD is reconstructed from explicit BJDREFI/BJDREFF and TDB headers through
    Astropy Time; no numerical-magnitude inference or alternate flux fallback.
    """
    if not 0 < len(content) <= config.limits.maximum_archive_product_bytes:
        raise DataValidationError(
            "FITS bytes: empty/oversized; supply a bounded pinned light-curve product"
        )
    if not content.startswith(b"SIMPLE  ="):
        raise DataValidationError(
            "FITS header: not uncompressed FITS; reject unsupported product bytes"
        )
    try:
        with fits.open(io.BytesIO(content), memmap=False, lazy_load_hdus=False) as hdus:
            primary, header = hdus[0].header, hdus[1].header
            if (
                not isinstance(hdus[1], fits.BinTableHDU)
                or type(header.get("NAXIS1")) is not int
                or type(header.get("NAXIS2")) is not int
                or header["NAXIS1"] <= 0
                or header["NAXIS2"] <= 0
                or header["NAXIS1"] * header["NAXIS2"] > len(content)
            ):
                raise DataValidationError(
                    "FITS table size: invalid/oversized; reject before loading table arrays"
                )
            table = hdus[1].data
            required = ("TIME", "PDCSAP_FLUX", "PDCSAP_FLUX_ERR", "QUALITY")
            if table is None or not set(required).issubset(table.names):
                raise DataValidationError(
                    "FITS columns: missing required PDCSAP/time/error/quality; reject product"
                )
            if (
                primary["TELESCOP"] != "TESS"
                or primary["SECTOR"] != config.data.sector
                or str(primary["TICID"]) != tic_id
                or "NASA/Ames" not in primary["ORIGIN"]
                or not str(primary["PROCVER"]).strip()
                or header["TIMESYS"].lower() != "tdb"
                or header["TIMEUNIT"].lower() != "d"
                or not np.isclose(header["TIMEDEL"] * 86400, 120, rtol=0, atol=1e-5)
            ):
                raise DataValidationError(
                    "FITS metadata: mission/TIC/sector/SPOC/cadence/time mismatch; reject product"
                )
            bjdref = float(header["BJDREFI"]) + float(header["BJDREFF"])
            if not np.isfinite(bjdref) or bjdref != 2457000.0:
                raise DataValidationError(
                    "FITS epoch: unsupported explicit BJD reference; inspect source metadata"
                )
            units = {name: table.columns[name].unit for name in required[:-1]}
            if (
                units["TIME"] != "d"
                or not units["PDCSAP_FLUX"]
                or units["PDCSAP_FLUX"] != units["PDCSAP_FLUX_ERR"]
            ):
                raise DataValidationError(
                    "FITS units: absent/inconsistent; require days and matching l"
                    "inear flux/error units"
                )
            # Check that the declared flux unit really is a linear count rate.
            # The SPOC spelling e-/s needs the same explicit alias used by Lightkurve.
            flux_unit = "electron/s" if units["PDCSAP_FLUX"] == "e-/s" else units["PDCSAP_FLUX"]
            if not u.Unit(flux_unit).is_equivalent(u.electron / u.second):
                raise DataValidationError(
                    "FITS flux units: unsupported; require explicit electron/s PDCSAP"
                )
            time, flux, error = (np.array(table[name], dtype=np.float64) for name in required[:-1])
            quality = np.array(table["QUALITY"])
            if any(a.shape != (len(table),) for a in (time, flux, error, quality)):
                raise DataValidationError(
                    "FITS columns: nonscalar rows; require scalar time/flux/error/quality"
                )
            processing = str(primary["PROCVER"])
            source_date = primary.get("DATE")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        if isinstance(error, DataValidationError):
            raise
        raise DataValidationError(
            "FITS structure: invalid/missing metadata; reject product and inspect archive identity"
        ) from error
    if quality.dtype.kind not in "iu" or np.any(quality < 0):
        raise DataValidationError(
            "FITS quality: malformed flags; require nonnegative integer quality"
        )
    bitmask = int(TessQualityFlags.OPTIONS[config.data.quality_bitmask])
    good_quality = TessQualityFlags.create_quality_mask(quality, bitmask=bitmask)
    reasons = {
        "quality_flagged": ~good_quality,
        "nonfinite_time": ~np.isfinite(time),
        "nonfinite_flux": ~np.isfinite(flux),
        "nonfinite_error": ~np.isfinite(error),
        "nonpositive_flux": flux <= 0,
        "nonpositive_error": error <= 0,
    }
    keep = ~np.logical_or.reduce(list(reasons.values()))
    audit = {
        "input_rows": len(time),
        "quality_selected_rows": int(good_quality.sum()),
        "retained_rows": int(keep.sum()),
        "removed_union_rows": int((~keep).sum()),
        "rejection_counts_overlap": True,
        "rejection_counts": {name: int(mask.sum()) for name, mask in reasons.items()},
        "quality_bitmask_name": config.data.quality_bitmask,
        "quality_bitmask_integer": bitmask,
        "lightkurve_version": version("lightkurve"),
        "astropy_version": version("astropy"),
        "flux_column": "PDCSAP_FLUX",
        "error_column": "PDCSAP_FLUX_ERR",
        "column_units": units,
        "bjd_reference": bjdref,
        "processing_version": processing,
        "source_creation_date": source_date,
    }
    if keep.sum() < 2:
        raise TessQCError("FITS retained rows: fewer than two; reject product", audit)
    time, flux, error = time[keep], flux[keep], error[keep]
    order = np.argsort(time, kind="stable")
    time, flux, error = time[order], flux[order], error[order]
    absolute = Time(bjdref, time, format="jd", scale="tdb")
    relative = (absolute - absolute[0]).to_value(u.day)
    digest = source_sha256(content)
    try:
        curve = prepare_curve(
            relative,
            flux,
            error,
            curve_id=f"tic{tic_id}-s0001-spoc-{digest[:16]}",
            source_sha256=digest,
            time_metadata=TimeMetadata(
                float(absolute[0].jd), "jd", "tdb", "d", units["PDCSAP_FLUX"], 1.0
            ),
            config=config.preparation,
        )
    except DataValidationError as error:
        raise TessQCError(str(error), audit) from error
    gaps = np.diff(curve.time_days)
    audit.update(
        {
            "source_start_btjd": float(time[0]),
            "source_end_btjd": float(time[-1]),
            "source_baseline_days": curve.original_end_days,
            "prepared_bins": len(curve.time_days),
            "normalization_median": curve.time_metadata.normalization_factor,
            "time_metadata": asdict(curve.time_metadata),
            "bin_minutes": config.preparation.bin_minutes,
            "bin_boundary_snap_tolerance": config.preparation.bin_boundary_snap_tolerance,
            "binning": "inverse_variance_weighted; anchor=first retained row; empty bins absent",
            "support_rows": int(curve.support_counts.sum()),
            "prepared_hash": curve.content_hash,
            "robust_scatter_mad": float(np.median(np.abs(curve.flux - np.median(curve.flux)))),
            "largest_prepared_gap_days": float(gaps.max()) if gaps.size else None,
            "gap_count_over_half_day": int((gaps > 0.5).sum()),
            "variability_note": (
                "Descriptive only; variability/quietness is not a selection filter."
            ),
        }
    )
    return curve, audit


def basic_qc_reasons(curve: PreparedCurve, audit: dict, config: ProtocolConfig) -> list[str]:
    """T02 only: T03 must subsequently apply all shared time-support eligibility checks."""
    reasons = []
    if audit["retained_rows"] < config.data.minimum_raw_points:
        reasons.append("fewer than 8000 retained raw observations")
    if curve.original_end_days < config.data.minimum_baseline_days:
        reasons.append("retained raw baseline shorter than 24 days")
    if len(curve.time_days) < config.data.minimum_binned_points:
        reasons.append("fewer than 2000 occupied 10-minute bins")
    return reasons
