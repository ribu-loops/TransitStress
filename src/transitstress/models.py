"""Scientific boundary contracts. No acquisition, injection, detrending or search execution."""

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

from transitstress.errors import DataValidationError
from transitstress.provenance import numeric_hash


def _require(condition: bool, field: str, action: str) -> None:
    if not condition:
        raise DataValidationError(f"{field}: invalid value; {action}")


def _number(value: object, field: str, positive: bool = False) -> None:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    _require(
        finite and (not positive or value > 0),
        field,
        "supply a finite positive number" if positive else "supply a finite number",
    )


def _count(value: int, field: str) -> None:
    _require(type(value) is int and value >= 0, field, "supply a nonnegative integer")


def _text(value: str, field: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), field, "supply a nonempty string")


def _choice(value: str, choices: tuple[str, ...], field: str) -> None:
    _require(type(value) is str and value in choices, field, "use a documented enum value")


def _source_array(value: ArrayLike, field: str) -> NDArray:
    try:
        return np.asarray(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise DataValidationError(
            f"{field}: invalid shape/type; supply a numeric vector"
        ) from error


def _sha(value: str, field: str) -> None:
    _require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        field,
        "supply the full lowercase SHA-256",
    )


def _vector(value: ArrayLike, field: str, positive: bool = False) -> NDArray[np.float64]:
    try:
        source = np.asarray(value)
        _require(source.dtype.kind in "iuf", field, "supply real numeric data")
        array = np.array(source, dtype="<f8", copy=True)
    except (TypeError, ValueError, OverflowError) as error:
        if isinstance(error, DataValidationError):
            raise
        raise DataValidationError(f"{field}: invalid array; supply finite real numbers") from error
    _require(array.ndim == 1 and array.size > 0, field, "supply a nonempty 1-D vector")
    _require(np.all(np.isfinite(array)), field, "supply finite values")
    if positive:
        _require(np.all(array > 0), field, "supply strictly positive values")
    array.setflags(write=False)
    return array


@dataclass(frozen=True)
class TimeMetadata:
    absolute_epoch: float | None
    time_format: str
    time_scale: str
    original_time_unit: str
    original_flux_unit: str
    normalization_factor: float
    core_time_unit: Literal["days"] = "days"
    core_flux_unit: Literal["dimensionless"] = "dimensionless"

    def __post_init__(self) -> None:
        if self.absolute_epoch is not None:
            _number(self.absolute_epoch, "absolute_epoch")
        _choice(self.time_format, ("jd", "mjd", "btjd", "unknown"), "time_format")
        _choice(self.time_scale, ("tdb", "utc", "tt", "tai", "unknown"), "time_scale")
        _choice(self.original_time_unit, ("d", "day", "days"), "original_time_unit")
        _text(self.original_flux_unit, "original_flux_unit")
        _number(self.normalization_factor, "normalization_factor", positive=True)
        _require(
            self.core_time_unit == "days" and self.core_flux_unit == "dimensionless",
            "core units",
            "use days and dimensionless flux/error",
        )
        _require(
            (self.absolute_epoch is None) == (self.time_format == "unknown"),
            "absolute_epoch/time_format",
            "use null/unknown together, or an explicit epoch/format",
        )
        if self.absolute_epoch is not None:
            _require(self.time_scale != "unknown", "time_scale", "supply the known epoch scale")


@dataclass(frozen=True)
class PreparedCurve:
    curve_id: str
    time_days: NDArray[np.float64]
    flux: NDArray[np.float64]
    flux_err: NDArray[np.float64]
    original_start_days: float
    original_end_days: float
    bin_anchor_days: float
    support_counts: NDArray[np.int64]
    time_metadata: TimeMetadata
    source_sha256: str

    def __post_init__(self) -> None:
        _text(self.curve_id, "curve_id")
        _sha(self.source_sha256, "source_sha256")
        _require(type(self.time_metadata) is TimeMetadata, "time_metadata", "supply TimeMetadata")
        for name in ("time_days", "flux", "flux_err"):
            object.__setattr__(self, name, _vector(getattr(self, name), name, name != "time_days"))
        _require(
            self.time_days.size == self.flux.size == self.flux_err.size,
            "curve lengths",
            "align time/flux/error",
        )
        _require(
            np.all(self.time_days[1:] > self.time_days[:-1]),
            "time_days",
            "sort and reject duplicate times",
        )
        for name in ("original_start_days", "original_end_days", "bin_anchor_days"):
            _number(getattr(self, name), name)
        _require(
            self.bin_anchor_days == self.original_start_days <= self.time_days[0]
            and self.time_days[-1] <= self.original_end_days
            and self.original_start_days < self.original_end_days,
            "original bounds",
            "preserve increasing original bounds and the original bin anchor",
        )
        support = _source_array(self.support_counts, "support_counts")
        _require(
            support.dtype.kind in "iu"
            and support.shape == self.time_days.shape
            and np.all(support > 0)
            and np.all(support <= 2**53),
            "support_counts",
            "supply one positive int64 count per prepared bin",
        )
        support = np.array(support, dtype="<i8", copy=True)
        support.setflags(write=False)
        object.__setattr__(self, "support_counts", support)
        with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
            weights = np.square(1.0 / self.flux_err)
        _require(
            np.all(np.isfinite(weights) & (weights > 0)),
            "flux_err weights",
            "supply errors with representable positive inverse-variance weights",
        )

    @property
    def content_hash(self) -> str:
        return numeric_hash(
            {
                "time_days": self.time_days,
                "flux": self.flux,
                "flux_err": self.flux_err,
                "support_counts": self.support_counts,
            },
            {
                "curve_id": self.curve_id,
                "source_sha256": self.source_sha256,
                "start": self.original_start_days,
                "end": self.original_end_days,
                "anchor": self.bin_anchor_days,
                "time_metadata": self.time_metadata,
            },
        )


@dataclass(frozen=True)
class InjectionSpec:
    family_id: str
    period_days: float
    duration_days: float
    epoch_days: float
    depth: float

    def __post_init__(self) -> None:
        _text(self.family_id, "family_id")
        _number(self.period_days, "period_days", positive=True)
        _number(self.duration_days, "duration_days", positive=True)
        _number(self.epoch_days, "epoch_days")
        _number(self.depth, "depth")
        _require(self.depth >= 0, "depth", "use a nonnegative relative decrement")
        _require(self.duration_days < self.period_days, "duration_days", "use duration < period")

    @property
    def depth_ppm(self) -> float:
        return self.depth * 1_000_000.0

    def validate_trial(self, *, workbench: bool = False) -> None:
        """Zero depth is internal only; local workbench has the declared narrower ranges."""
        _require(self.depth > 0, "depth", "use positive depth for public trials")
        if workbench:
            _require(1 <= self.period_days <= 8, "period_days", "use 1–8 days")
            _require(100 <= self.depth_ppm <= 5000, "depth_ppm", "use 100–5000 ppm")
            _require(1 / 24 <= self.duration_days <= 6 / 24, "duration_days", "use 1–6 hours")


@dataclass(frozen=True)
class ObservationMask:
    keep: NDArray[np.bool_]
    definition: Literal["original", "random_missing", "central_block"]
    seed: int | None

    def __post_init__(self) -> None:
        source = _source_array(self.keep, "keep")
        _require(
            source.dtype == bool and source.ndim == 1 and source.size > 0,
            "keep",
            "supply a nonempty 1-D boolean mask aligned to original indices",
        )
        _choice(self.definition, ("original", "random_missing", "central_block"), "definition")
        if self.definition == "random_missing":
            _require(
                type(self.seed) is int and 0 <= self.seed < 2**64,
                "seed",
                "supply an unsigned 64-bit seed for random removal",
            )
        else:
            _require(self.seed is None, "seed", "use null for deterministic masks")
        if self.definition == "original":
            _require(np.all(source), "keep", "retain all original observations")
        array = source.copy()
        array.setflags(write=False)
        object.__setattr__(self, "keep", array)

    def validate_alignment(self, curve: PreparedCurve) -> None:
        _require(
            self.keep.shape == curve.time_days.shape, "keep", "align to original prepared indices"
        )

    @property
    def removed_count(self) -> int:
        return int(self.keep.size - np.count_nonzero(self.keep))

    @property
    def checksum(self) -> str:
        return numeric_hash({"keep": self.keep})


@dataclass(frozen=True)
class DetrendConfig:
    """One of the two pipelines, with no truth-bearing input."""

    pipeline_id: Literal["rm_0p5d", "rm_1p5d"]
    window_days: float
    method: str = "centered_time_rolling_median"
    minimum_neighbors: int = 5
    segment_gap_days: float = 0.5

    def __post_init__(self) -> None:
        _number(self.window_days, "window_days", positive=True)
        _require(
            (self.pipeline_id, self.window_days) in (("rm_0p5d", 0.5), ("rm_1p5d", 1.5)),
            "pipeline_id/window_days",
            "use rm_0p5d/0.5 or rm_1p5d/1.5",
        )
        _require(
            self.method == "centered_time_rolling_median",
            "method",
            "use the protocol rolling median",
        )
        _require(
            type(self.minimum_neighbors) is int and self.minimum_neighbors == 5,
            "minimum_neighbors",
            "use integer 5",
        )
        _number(self.segment_gap_days, "segment_gap_days", positive=True)
        _require(self.segment_gap_days == 0.5, "segment_gap_days", "use 0.5 days")


@dataclass(frozen=True)
class PeriodGrid:
    periods_days: NDArray[np.float64]
    original_baseline_days: float
    search_config_hash: str

    def __post_init__(self) -> None:
        array = _vector(self.periods_days, "periods_days", positive=True)
        _require(
            array.size <= 50000 and np.all(np.diff(array) > 0),
            "periods_days",
            "use <=50000 strictly increasing periods",
        )
        _require(
            array[0] >= 0.75 and array[-1] <= 10,
            "periods_days",
            "use independent 0.75–10 day bounds",
        )
        _number(self.original_baseline_days, "original_baseline_days", positive=True)
        _sha(self.search_config_hash, "search_config_hash")
        object.__setattr__(self, "periods_days", array)

    @property
    def checksum(self) -> str:
        return numeric_hash(
            {"periods_days": self.periods_days},
            {
                "baseline_days": self.original_baseline_days,
                "search_config_hash": self.search_config_hash,
            },
        )


@dataclass(frozen=True)
class Measurement:
    """Null measurements always carry a reason, finite measurements never do."""

    value: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            _text(self.reason, "measurement.reason")
        else:
            _number(self.value, "measurement.value")
            _require(self.reason is None, "measurement.reason", "use null for a measured value")


@dataclass(frozen=True)
class SearchResult:
    status: Literal["ok", "error"]
    period_days: float | None
    epoch_days: float | None
    duration_days: float | None
    depth: float | None
    depth_err: float | None
    depth_snr: float | None
    power: float | None
    grid_hash: str
    elapsed_seconds: float
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        _sha(self.grid_hash, "grid_hash")
        _number(self.elapsed_seconds, "elapsed_seconds")
        _require(self.elapsed_seconds >= 0, "elapsed_seconds", "use nonnegative elapsed seconds")
        names = (
            "period_days",
            "epoch_days",
            "duration_days",
            "depth",
            "depth_err",
            "depth_snr",
            "power",
        )
        _choice(self.status, ("ok", "error"), "status")
        if self.status == "error":
            _text(self.failure_reason, "failure_reason")
            _require(
                all(getattr(self, name) is None for name in names),
                "search diagnostics",
                "use null for a failed search",
            )
        else:
            _require(self.failure_reason is None, "failure_reason", "use null on successful search")
            for name in names:
                _number(
                    getattr(self, name), name, name in {"period_days", "duration_days", "depth_err"}
                )
            _require(
                self.duration_days < self.period_days, "duration_days", "use duration < period"
            )


@dataclass(frozen=True)
class EvaluationResult:
    status: Literal["exact", "other_peak", "insufficient_coverage", "error", "control"]
    observed_transits: int
    total_in_transit_points: int
    partial_edge_events: int
    exact_recovery: bool | None
    relative_period_error: Measurement
    maximum_timing_error_days: Measurement
    half_period_peak: bool | None
    double_period_peak: bool | None
    half_alias_searchable: bool | None
    double_alias_searchable: bool | None
    retention_ratio: Measurement
    retained_depth: Measurement
    background_already_matches: bool | None
    exact_after_injection_without_background_match: bool | None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        _choice(
            self.status,
            ("exact", "other_peak", "insufficient_coverage", "error", "control"),
            "evaluation.status",
        )
        for name in ("observed_transits", "total_in_transit_points", "partial_edge_events"):
            _count(getattr(self, name), name)
        for name in (
            "relative_period_error",
            "maximum_timing_error_days",
            "retention_ratio",
            "retained_depth",
        ):
            _require(
                type(getattr(self, name)) is Measurement, name, "supply Measurement(value, reason)"
            )
        for name in (
            "exact_recovery",
            "half_period_peak",
            "double_period_peak",
            "half_alias_searchable",
            "double_alias_searchable",
            "background_already_matches",
            "exact_after_injection_without_background_match",
        ):
            _require(
                getattr(self, name) is None or type(getattr(self, name)) is bool,
                name,
                "use boolean or null",
            )
        if self.status in {"error", "insufficient_coverage", "control"}:
            _text(self.failure_reason, "evaluation.failure_reason")
            _require(
                self.exact_recovery is None, "exact_recovery", "use null for unevaluated recovery"
            )
        else:
            _require(
                self.failure_reason is None and self.exact_recovery is (self.status == "exact"),
                "exact_recovery",
                "match the declared outcome without a failure reason",
            )


@dataclass(frozen=True)
class RunManifest:
    schema_version: int
    protocol_id: str
    config_hash: str
    data_hash: str
    lock_hash: str
    scientific_source_hash: str
    execution_commit: str
    started_utc: str
    finished_utc: str | None
    platform: str
    commands: tuple[str, ...]
    split: Literal["development", "evaluation", "exploratory"]
    expected_rows: int
    actual_rows: int
    status: Literal["incomplete", "complete"]
    file_checksums: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require(
            type(self.schema_version) is int and self.schema_version == 1,
            "schema_version",
            "use integer 1",
        )
        _text(self.protocol_id, "protocol_id")
        for name in ("config_hash", "data_hash", "lock_hash", "scientific_source_hash"):
            _sha(getattr(self, name), name)
        _require(
            isinstance(self.execution_commit, str)
            and re.fullmatch(r"[0-9a-f]{40}", self.execution_commit) is not None,
            "execution_commit",
            "record the actual full execution SHA-1",
        )
        _text(self.platform, "platform")
        _text(self.started_utc, "started_utc")
        _require(
            type(self.commands) is tuple and bool(self.commands),
            "commands",
            "record explicit commands as a tuple",
        )
        for command in self.commands:
            _text(command, "command")
        _choice(self.split, ("development", "evaluation", "exploratory"), "split")
        _count(self.expected_rows, "expected_rows")
        _count(self.actual_rows, "actual_rows")
        _require(
            0 < self.expected_rows <= 1000 and self.actual_rows <= self.expected_rows,
            "row counts",
            "retain all expected rows within the 1000-row budget",
        )
        _choice(self.status, ("incomplete", "complete"), "status")
        _require(
            self.status != "complete"
            or (self.actual_rows == self.expected_rows and self.finished_utc is not None),
            "completion",
            "complete all rows and record finish time",
        )
        times = []
        for value in (self.started_utc, self.finished_utc):
            if value is not None:
                try:
                    parsed = datetime.fromisoformat(value)
                    _require(
                        parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0,
                        "UTC timestamp",
                        "use an explicit UTC offset",
                    )
                except (ValueError, TypeError) as error:
                    raise DataValidationError(
                        "UTC timestamp: invalid; supply ISO-8601 UTC"
                    ) from error
                times.append(parsed)
        _require(len(times) < 2 or times[1] >= times[0], "timestamps", "finish at or after start")
        _require(
            type(self.file_checksums) is tuple, "file_checksums", "use immutable name/hash pairs"
        )
        _require(
            self.status != "complete" or bool(self.file_checksums),
            "file_checksums",
            "record checksums before completion",
        )
        names = []
        for pair in self.file_checksums:
            _require(
                type(pair) is tuple and len(pair) == 2, "file_checksums", "use name/hash pairs"
            )
            name, checksum = pair
            _text(name, "artifact name")
            _sha(checksum, "artifact checksum")
            names.append(name)
        _require(len(names) == len(set(names)), "file_checksums", "use unique artifact names")
