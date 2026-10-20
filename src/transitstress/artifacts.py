"""Bounded scientific JSON/CSV/NPZ with atomic writes and header-first reads."""

import csv
import io
import json
import math
import re
import struct
import tokenize
import zipfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from transitstress import acquisition as acq
from transitstress.errors import DataValidationError
from transitstress.models import EvaluationResult, InjectionSpec, Measurement, SearchResult
from transitstress.provenance import canonical_hash, canonical_json, numeric_hash

MAX_BYTES = 8 * 1024**2
NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
PIPELINES = ("rm_0p5d", "rm_1p5d")
STATUSES = ("ok", "invalid_input", "detrend_error", "search_error", "artifact_error")
KINDS = ("injection", "uninjected_real", "gaussian_null")


def json_read(root: Path, name: str) -> dict:
    """Reject duplicate keys, nonfinite numbers and oversized metadata."""

    def unique(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        path = acq.safe_file(root, name)
        with path.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("size")
        value = json.loads(content, object_pairs_hook=unique)
        if type(value) is not dict:
            raise ValueError("schema")
        canonical_json(value)
        return value
    except (OSError, ValueError) as error:
        raise DataValidationError(
            "artifact JSON: invalid/missing; preserve and diagnose"
        ) from error


def json_write(root: Path, name: str, value: object) -> str:
    content = canonical_json(value) + b"\n"
    if len(content) > MAX_BYTES:
        raise DataValidationError("artifact JSON: over 8 MiB; reduce bounded evidence")
    acq.atomic_write(root, name, content)
    return acq.source_sha256(content)


def _numeric(arrays: dict[str, NDArray]) -> dict[str, NDArray]:
    if not 1 <= len(arrays) <= 80 or any(not NAME.fullmatch(k) for k in arrays):
        raise DataValidationError("numeric names/count: invalid; use 1–80 internal array names")
    result = {}
    for key, source in arrays.items():
        a = np.asarray(source)
        if a.ndim != 1 or not 1 <= a.size <= 50000 or a.dtype.kind not in "biuf":
            raise DataValidationError("numeric shape/dtype: invalid; use bounded real vectors")
        if not np.all(np.isfinite(a)):
            raise DataValidationError("numeric values: nonfinite; encode explicit validity first")
        numeric_hash({key: a})  # includes integer representability and mask validation
        result[key] = np.asarray(a, dtype="u1" if a.dtype.kind == "b" else "<f8")
    if sum(a.nbytes for a in result.values()) > 16 * 1024**2:
        raise DataValidationError("numeric payload: over 16 MiB; reduce evidence")
    return result


def numeric_write(root: Path, name: str, arrays: dict[str, NDArray]) -> dict:
    bounded = _numeric(arrays)
    stream = io.BytesIO()
    np.savez_compressed(stream, **bounded)
    content = stream.getvalue()
    if len(content) > MAX_BYTES:
        raise DataValidationError("numeric transport: over 8 MiB; reduce evidence")
    acq.atomic_write(root, name, content)
    return dict(
        transport_sha256=acq.source_sha256(content),
        numeric_hash=numeric_hash(bounded),
        arrays={k: dict(length=len(v), dtype=v.dtype.str) for k, v in bounded.items()},
    )


def _preflight(stream: object, member_size: int) -> tuple:
    """Adapted from the independently repaired internal scripts/t04_arrays.py.

    Every header passes before np.load or any numeric payload allocation.
    """
    version = np.lib.format.read_magic(stream)
    if version not in ((1, 0), (2, 0)):
        raise ValueError("version")
    width = 2 if version == (1, 0) else 4
    raw = stream.read(width)
    if len(raw) != width:
        raise ValueError("header length")
    length = struct.unpack("<H" if width == 2 else "<I", raw)[0]
    if not 0 < length <= 512 or 8 + width + length > member_size:
        raise ValueError("header bounds")
    stream.seek(8)
    reader = (
        np.lib.format.read_array_header_1_0 if width == 2 else np.lib.format.read_array_header_2_0
    )
    shape, order, dtype = reader(stream, max_header_size=512)
    if (
        len(shape) != 1
        or type(shape[0]) is not int
        or not 1 <= shape[0] <= 50000
        or dtype.str not in ("<f8", "|u1")
        or dtype.hasobject
        or order
        or dtype.fields is not None
        or dtype.subdtype is not None
    ):
        raise ValueError("header schema")
    if member_size - stream.tell() != shape[0] * dtype.itemsize:
        raise ValueError("payload length")
    return shape, dtype


def numeric_read(root: Path, name: str, record: dict) -> dict[str, NDArray]:
    if type(record) is not dict:
        raise DataValidationError("numeric record: invalid schema; restore generated metadata")
    path = acq.safe_file(root, name)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise DataValidationError("numeric file: missing/oversized; restore bounded evidence")
    content = path.read_bytes()
    if acq.source_sha256(content) != record.get("transport_sha256"):
        raise DataValidationError("numeric checksum: mismatch; preserve corrupt evidence")
    try:
        schema = record["arrays"]
        if not 1 <= len(schema) <= 80 or any(not NAME.fullmatch(k) for k in schema):
            raise ValueError("names")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if (
                len(members) != len(schema)
                or {m.filename for m in members} != {f"{k}.npy" for k in schema}
                or any(m.file_size > 50000 * 8 + 512 for m in members)
                or sum(m.file_size for m in members) > 16 * 1024**2
            ):
                raise ValueError("members")
            headers = {}
            for key in schema:
                member = archive.getinfo(f"{key}.npy")
                with archive.open(member) as stream:
                    shape, dtype = _preflight(stream, member.file_size)
                if schema[key] != dict(length=shape[0], dtype=dtype.str):
                    raise ValueError("declared schema")
                headers[key] = shape, dtype
        with np.load(io.BytesIO(content), allow_pickle=False, max_header_size=512) as stored:
            arrays = {key: stored[key] for key in schema}
        for key, array in arrays.items():
            if (array.shape, array.dtype) != headers[key]:
                raise ValueError("decoded schema")
        _numeric(arrays)
        if numeric_hash(arrays) != record["numeric_hash"]:
            raise ValueError("content identity")
    except (
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        SyntaxError,
        tokenize.TokenError,
        zipfile.BadZipFile,
        EOFError,
        NotImplementedError,
        RuntimeError,
    ) as error:
        raise DataValidationError("numeric schema: invalid; preserve and diagnose") from error
    return arrays


def validate_rows(rows: list[dict], planned: list[dict]) -> None:
    expected = {(p["case_id"], pipe) for p in planned for pipe in PIPELINES}
    actual = [(r["case_id"], r["pipeline_id"]) for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected or len(actual) > 1000:
        raise DataValidationError("result IDs: missing/extra/duplicate; retain planned pairs")
    for row in rows:
        if row["status"] not in STATUSES or row["case_kind"] not in KINDS:
            raise DataValidationError("result schema: invalid status/kind; restore typed rows")
        for value in row.values():
            if type(value) is dict and set(value) == {"value", "reason"}:
                if (value["value"] is None) != (type(value["reason"]) is str):
                    raise DataValidationError("measurement: missing reason; use null/reason")
        canonical_json(row)
        SearchResult(**row["search"])
        evaluation = dict(row["evaluation"])
        for key in (
            "relative_period_error",
            "maximum_timing_error_days",
            "retained_depth",
            "retention_ratio",
        ):
            evaluation[key] = Measurement(**evaluation[key])
        for key in (
            "full_centers_days",
            "full_event_points",
            "observed_centers_days",
            "partial_centers_days",
            "partial_event_points",
        ):
            evaluation[key] = tuple(evaluation[key])
        EvaluationResult(**evaluation)


def validate_plan(plan: list[dict]) -> None:
    if not 1 <= len(plan) <= 500 or len({c["case_id"] for c in plan}) != len(plan):
        raise DataValidationError("case plan: empty/duplicate/oversized; enumerate bounded pairs")
    for case in plan:
        if (
            not re.fullmatch(r"[0-9a-f]{64}", case["case_id"])
            or case["kind"] not in KINDS
            or case["mask_definition"] not in ("original", "random_missing", "central_block")
        ):
            raise DataValidationError("case plan: unsafe IDs/enums; use generated plan")
        for key in ("prepared_hash", "mask_hash"):
            if not re.fullmatch(r"[0-9a-f]{64}", case[key]):
                raise DataValidationError("case plan: invalid parent/mask hash; use generated plan")
        for key in ("seed", "sigma", "replicate"):
            value = case.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise DataValidationError(
                    "case settings: invalid number; use finite seed/sigma/replicate"
                )
        if case["kind"] == "injection":
            InjectionSpec(**case["injection"]).validate_trial()
        elif case["injection"] is not None:
            raise DataValidationError("control truth: forbidden; use null injection")


# The scientific spreadsheet schema is deliberately fixed: arbitrary names,
# paths, failure messages, metadata and extra input columns never enter CSV.
CSV_ENUMS = {
    "split": ("development", "evaluation", "exploratory"),
    "pipeline_id": PIPELINES,
    "case_kind": KINDS,
    "status": STATUSES,
    "mask_name": ("original", "random_missing", "central_block"),
    "scientific_outcome": (
        "exact",
        "half_period_peak",
        "double_period_peak",
        "other_peak",
        "insufficient_coverage",
        "not_applicable",
    ),
}
CSV_NUMBERS = (
    "n_original",
    "n_observed",
    "actual_removed_fraction",
    "seed",
    "sigma",
    "detrend_window_days",
    "detrend_seconds",
    "search_seconds",
    "period_days",
    "epoch_days",
    "duration_days",
    "depth",
    "depth_err",
    "depth_snr",
    "power",
    "true_period_days",
    "true_epoch_days",
    "true_duration_days",
    "true_depth",
    "coverage_eligible",
    "exact_recovery",
    "full_events",
    "observed_transits",
    "total_in_transit_points",
    "background_already_matches",
    "exact_after_injection_without_background_match",
    "relative_period_error",
    "maximum_timing_error_days",
    "retained_depth",
    "retention_ratio",
)
CSV_HASHES = ("case_id", "run_id", "period_grid_hash", "mask_hash")
CSV_FIELDS = (*CSV_HASHES, *CSV_ENUMS, *CSV_NUMBERS)


def results_csv(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        result = {}
        for key in CSV_FIELDS:
            value = row.get(key)
            if type(value) is dict:
                value = value["value"]
            if key in CSV_HASHES:
                valid = type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
            elif key in CSV_ENUMS:
                valid = value in CSV_ENUMS[key]
            else:
                valid = value is None or type(value) in (float, int, bool)
            if not valid:
                raise DataValidationError("CSV field: invalid type/enum; exclude untrusted text")
            result[key] = int(value) if type(value) is bool else value
        canonical_json(result)
        writer.writerow(result)
    return stream.getvalue().encode("utf-8")


def read_results_csv(content: bytes) -> list[dict]:
    """Typed numeric projection; JSON retains reasons, nested metadata and units."""
    if not 0 < len(content) <= MAX_BYTES:
        raise DataValidationError("CSV evidence: empty/oversized; restore bounded export")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
        if reader.fieldnames != list(CSV_FIELDS):
            raise ValueError("headers")
        rows = []
        for record in reader:
            if len(rows) >= 1000 or None in record or any(v is None for v in record.values()):
                raise ValueError("rows")
            result = {}
            for key, value in record.items():
                if key in CSV_HASHES:
                    if not re.fullmatch(r"[0-9a-f]{64}", value):
                        raise ValueError("hash")
                    result[key] = value
                elif key in CSV_ENUMS:
                    if value not in CSV_ENUMS[key]:
                        raise ValueError("enum")
                    result[key] = value
                else:
                    # JSON number grammar rejects spreadsheet expressions, Python
                    # literals and nonfinite tokens. Large uint64 seeds stay integers.
                    number = None if value == "" else json.loads(value)
                    if number is not None and type(number) not in (int, float):
                        raise ValueError("number")
                    result[key] = number
            canonical_json(result)
            rows.append(result)
        return rows
    except (ValueError, UnicodeError, csv.Error, TypeError) as error:
        raise DataValidationError(
            "CSV evidence: malformed schema/value; restore typed export"
        ) from error


def verify_checksum(root: Path, name: str, expected: str) -> bool:
    path = acq.safe_file(root, name)
    return (
        path.is_file()
        and path.stat().st_size <= 32 * 1024**2
        and acq.source_sha256(path.read_bytes()) == expected
    )


def case_digest(rows: list[dict]) -> str:
    return canonical_hash(rows)


def validate_numeric_inputs(root: Path) -> None:
    path = acq.safe_file(root, "numeric_inputs.json")
    if not path.exists():
        return
    files = json_read(root, "numeric_inputs.json")["files"]
    if not 1 <= len(files) <= 40:
        raise DataValidationError("numeric inputs: invalid count; restore bounded snapshots")
    for name, record in files.items():
        parts = name.split("/")
        if len(parts) != 2 or parts[0] not in ("inputs", "grids", "masks"):
            raise DataValidationError("numeric input path: invalid; restore generated snapshots")
        numeric_read(root / parts[0], parts[1], record)
