"""Strict bounded UTF-8 CSV boundary; uploaded names never become filesystem paths."""

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from transitstress.config import ProtocolConfig
from transitstress.errors import DataValidationError
from transitstress.models import PreparedCurve, TimeMetadata
from transitstress.preparation import prepare_curve

REQUIRED = ("time_days", "flux", "flux_err")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@dataclass(frozen=True)
class CSVInput:
    curve: PreparedCurve
    input_rows: int
    source_start_days: float
    source_end_days: float
    notices: tuple[str, ...]


def parse_csv(
    content: bytes, *, confirm_days_and_linear_units: bool, config: ProtocolConfig
) -> CSVInput:
    """Parse upload bytes only. Unknown absolute epochs stay unknown, even near BTJD."""
    if confirm_days_and_linear_units is not True:
        raise DataValidationError(
            "CSV units: unconfirmed; confirm days and linear flux/error units"
        )
    if type(content) is not bytes or not 0 < len(content) <= config.limits.maximum_csv_bytes:
        raise DataValidationError(
            "CSV bytes: empty/oversized; supply at most 5 MiB uncompressed CSV"
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError as error:
        raise DataValidationError(
            "CSV encoding: invalid UTF-8; save uncompressed UTF-8 CSV"
        ) from error
    if "\0" in text:
        raise DataValidationError("CSV bytes: NUL found; supply plain UTF-8 CSV")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows: list[list[float]] = []
    try:
        headers = next(reader, [])
        if len(set(headers)) != len(headers) or not set(REQUIRED).issubset(headers):
            raise DataValidationError(
                "CSV headers: missing/duplicate; use unique time_days,flux,flux_err"
            )
        positions = [headers.index(name) for name in REQUIRED]
        for row_number, row in enumerate(reader, start=1):
            if row_number > config.limits.maximum_csv_rows:
                raise DataValidationError(
                    "CSV rows: limit exceeded; supply at most 50,000 data rows"
                )
            if len(row) != len(headers):
                raise DataValidationError(
                    f"CSV row {row_number}: inconsistent width; fix every cell"
                )
            values = [row[p].strip() for p in positions]
            if any(NUMBER.fullmatch(value) is None for value in values):
                raise DataValidationError(
                    f"CSV row {row_number}: invalid numeric cell; use finite numbers"
                )
            numbers = [float(value) for value in values]
            if not all(np.isfinite(numbers)) or numbers[1] <= 0 or numbers[2] <= 0:
                raise DataValidationError(
                    f"CSV row {row_number}: invalid range; use positive finite flux/errors"
                )
            rows.append(numbers)
    except csv.Error as error:
        raise DataValidationError(
            "CSV syntax: malformed/field too large; supply valid bounded CSV"
        ) from error
    if len(rows) < 2:
        raise DataValidationError("CSV rows: insufficient data; supply a 24–40 day curve")
    t, f, e = np.array(rows, dtype=np.float64).T
    with np.errstate(all="ignore"):
        baseline = float(t.max() - t.min())
    if not config.data.minimum_baseline_days <= baseline <= config.limits.maximum_csv_baseline_days:
        raise DataValidationError(
            "CSV baseline: outside 24–40 days; supply explicit day-valued times"
        )
    source_hash = hashlib.sha256(content).hexdigest()
    curve = prepare_curve(
        t,
        f,
        e,
        curve_id=f"csv-{source_hash[:16]}",
        source_sha256=source_hash,
        time_metadata=TimeMetadata(None, "unknown", "unknown", "days", "user linear units", 1.0),
        config=config.preparation,
    )
    if len(curve.time_days) < config.data.minimum_binned_points:
        raise DataValidationError(
            "CSV bins: fewer than 2,000 occupied bins; supply sufficient sampling"
        )
    extra = len(headers) - len(REQUIRED)
    return CSVInput(
        curve,
        len(rows),
        float(t.min()),
        float(t.max()),
        (f"Ignored {extra} extra columns; contents are not retained or exported.",)
        if extra
        else (),
    )


def read_local_csv(
    path: str | Path, *, confirm_days_and_linear_units: bool, config: ProtocolConfig
) -> CSVInput:
    """Explicit CLI path only; reject URI, UNC/device and nonregular inputs before opening."""
    name = str(path)
    if (
        re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", name) and not re.match(r"^[a-zA-Z]:[\\/]", name)
    ) or name.startswith(("\\\\", "//")):
        raise DataValidationError(
            "CSV path: remote/URI input forbidden; use a regular local .csv file"
        )
    file = Path(path).resolve()
    if file.suffix.lower() != ".csv" or not file.is_file():
        raise DataValidationError("CSV path: not a regular local CSV; choose an existing .csv file")
    try:
        if file.stat().st_size > config.limits.maximum_csv_bytes:
            raise DataValidationError("CSV bytes: oversized; supply at most 5 MiB")
        with file.open("rb") as stream:
            content = stream.read(config.limits.maximum_csv_bytes + 1)
    except OSError as error:
        raise DataValidationError("CSV path: unreadable; choose an accessible local CSV") from error
    return parse_csv(
        content, confirm_days_and_linear_units=confirm_days_and_linear_units, config=config
    )
