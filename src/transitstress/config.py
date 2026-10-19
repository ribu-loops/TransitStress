"""Strict, immutable protocol-v1 settings; no execution or file discovery."""

import math
import os
import tomllib
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from transitstress.errors import ConfigurationError


def _finite(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _typed(value: object, annotation: object, field: str) -> None:
    if get_origin(annotation) is tuple:
        if type(value) is not tuple:
            raise ConfigurationError(f"{field}: invalid type; supply a sequence")
        for index, item in enumerate(value):
            _typed(item, get_args(annotation)[0], f"{field}[{index}]")
    elif annotation is float:
        if not _finite(value):
            raise ConfigurationError(f"{field}: invalid number; supply a finite numeric value")
    elif type(value) is not annotation:
        raise ConfigurationError(f"{field}: invalid type; use the documented schema type")


class _ProtocolSection:
    """V1 scientific choices are pinned, not a general experiment language."""

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            _typed(value, get_type_hints(type(self))[field.name], field.name)
            if value != field.default:
                raise ConfigurationError(
                    f"{field.name}: unsupported protocol-v1 setting; "
                    f"use {field.default!r} or document a versioned protocol amendment"
                )


@dataclass(frozen=True)
class DataConfig(_ProtocolSection):
    mission: str = "TESS"
    sector: int = 1
    author: str = "SPOC"
    exposure_seconds: int = 120
    flux_column: str = "pdcsap_flux"
    quality_bitmask: str = "default"
    development_stars: int = 2
    evaluation_stars: int = 5
    minimum_baseline_days: float = 24.0
    minimum_raw_points: int = 8000
    minimum_binned_points: int = 2000
    maximum_candidate_downloads: int = 30


@dataclass(frozen=True)
class SanityConfig(_ProtocolSection):
    tic_id: str = "261136679"
    sector: int = 1
    reference_period_days_approx: float = 6.27


@dataclass(frozen=True)
class PreparationConfig(_ProtocolSection):
    bin_minutes: float = 10.0
    bin_boundary_snap_tolerance: float = 1e-8
    bin_aggregation: str = "inverse_variance_weighted"
    inject_after_binning: bool = True
    interpolate_missing: bool = False
    post_injection_sigma_clip: bool = False


@dataclass(frozen=True)
class InjectionFamily:
    id: str
    period_days: float
    epoch_fraction: float

    def __post_init__(self) -> None:
        _typed(self.id, str, "family.id")
        _typed(self.period_days, float, "family.period_days")
        _typed(self.epoch_fraction, float, "family.epoch_fraction")
        if not self.id or self.period_days <= 0 or not 0 <= self.epoch_fraction < 1:
            raise ConfigurationError("family: invalid ID/period/phase; use ID, P>0, 0<=phase<1")


@dataclass(frozen=True)
class InjectionConfig(_ProtocolSection):
    model: str = "additive_box"
    depths_ppm: tuple[float, ...] = (250.0, 750.0, 2250.0)
    duration_hours: float = 3.0
    families: tuple[InjectionFamily, ...] = (
        InjectionFamily("F1", 1.73, 0.17),
        InjectionFamily("F2", 3.41, 0.43),
        InjectionFamily("F3", 6.83, 0.71),
    )


@dataclass(frozen=True)
class MaskConfig(_ProtocolSection):
    names: tuple[str, ...] = ("original", "random_missing", "central_block")
    random_removed_fraction: float = 0.20
    block_start_fraction: float = 0.40
    block_end_fraction: float = 0.60


@dataclass(frozen=True)
class DetrendingConfig(_ProtocolSection):
    method: str = "centered_time_rolling_median"
    window_days: tuple[float, ...] = (0.5, 1.5)
    minimum_neighbors: int = 5
    segment_gap_days: float = 0.5
    include_window_endpoints: bool = True
    normalize_output_again: bool = False


@dataclass(frozen=True)
class SearchConfig(_ProtocolSection):
    """Independent settings; truth is not an accepted field. Grid is a separate contract."""

    minimum_period_days: float = 0.75
    maximum_period_days: float = 10.0
    duration_hours: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 6.0)
    objective: str = "likelihood"
    method: str = "fast"
    oversample: int = 10
    frequency_factor: float = 1.0
    reuse_original_grid_across_masks: bool = True

    @property
    def duration_days(self) -> tuple[float, ...]:
        return tuple(hours / 24.0 for hours in self.duration_hours)


@dataclass(frozen=True)
class EvaluationConfig(_ProtocolSection):
    relative_period_tolerance: float = 0.01
    maximum_center_error_duration_fraction: float = 0.5
    minimum_points_per_observed_transit: int = 3
    minimum_observed_transits: int = 2
    minimum_total_in_transit_points: int = 6
    minimum_retention_in_points: int = 3
    minimum_retention_out_points: int = 3


@dataclass(frozen=True)
class ControlConfig(_ProtocolSection):
    include_uninjected_real: bool = True
    gaussian_replicates_per_star_mask: int = 3
    gaussian_sigma_source: str = "median_prepared_flux_uncertainty"


@dataclass(frozen=True)
class ExecutionConfig(_ProtocolSection):
    workers: int = 1
    resume_only_matching_run_identity: bool = True
    allow_network_during_analysis: bool = False


@dataclass(frozen=True)
class LimitsConfig(_ProtocolSection):
    maximum_csv_bytes: int = 5242880
    maximum_csv_rows: int = 50000
    maximum_csv_baseline_days: float = 40.0
    maximum_period_samples: int = 50000
    maximum_result_rows: int = 1000
    maximum_archive_product_bytes: int = 52428800
    maximum_acquisition_bytes: int = 1073741824


@dataclass(frozen=True)
class ProtocolConfig:
    schema_version: int
    protocol_id: str
    frozen: bool
    master_seed: int
    data: DataConfig
    sanity: SanityConfig
    preparation: PreparationConfig
    injection: InjectionConfig
    masks: MaskConfig
    detrending: DetrendingConfig
    search: SearchConfig
    evaluation: EvaluationConfig
    controls: ControlConfig
    execution: ExecutionConfig
    limits: LimitsConfig

    def __post_init__(self) -> None:
        for field in fields(self):
            _typed(getattr(self, field.name), get_type_hints(type(self))[field.name], field.name)
        if self.schema_version != 1 or self.protocol_id != "transitstress-v1":
            raise ConfigurationError(
                "protocol identity: unsupported; use schema 1/transitstress-v1"
            )
        if not 0 <= self.master_seed < 2**64:
            raise ConfigurationError("master_seed: out of range; use an unsigned 64-bit integer")

    @property
    def minimum_period_days(self) -> float:
        """Compatibility for the T00 doctor consumer."""
        return self.search.minimum_period_days

    @property
    def maximum_period_days(self) -> float:
        return self.search.maximum_period_days


def _decode(value: object, annotation: type, field: str) -> object:
    if is_dataclass(annotation):
        if type(value) is not dict:
            raise ConfigurationError(f"{field}: missing/invalid table; supply all schema fields")
        if any(type(key) is not str for key in value):
            raise ConfigurationError(f"{field}: invalid key type; use string schema keys")
        expected = get_type_hints(annotation)
        if value.keys() != expected.keys():
            missing = sorted(expected.keys() - value.keys())
            unknown = sorted(value.keys() - expected.keys())
            raise ConfigurationError(
                f"{field}: missing keys {missing}, unknown keys {unknown}; use only schema fields"
            )
        decoded = {
            key: _decode(item, expected[key], f"{field}.{key}") for key, item in value.items()
        }
        try:
            return annotation(**decoded)
        except ConfigurationError as error:
            raise ConfigurationError(f"{field}.{error}") from error
    if get_origin(annotation) is tuple:
        if type(value) not in (list, tuple):
            raise ConfigurationError(f"{field}: invalid sequence; supply an array")
        return tuple(
            _decode(item, get_args(annotation)[0], f"{field}[{index}]")
            for index, item in enumerate(value)
        )
    _typed(value, annotation, field)
    return float(value) if annotation is float else value


def validate_config(raw: dict[str, object]) -> ProtocolConfig:
    """Reject missing/unknown keys, bad types and undeclared v1 science choices."""
    return _decode(raw, ProtocolConfig, "config")


def load_config(path: Path) -> ProtocolConfig:
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError("config: unreadable or invalid TOML; check the file") from error
    return validate_config(raw)


def app_mode() -> str:
    """Read only the explicitly allowed nonsecret setting."""
    mode = os.environ.get("TRANSITSTRESS_MODE", "demo")
    if mode not in {"demo", "local"}:
        raise ConfigurationError("TRANSITSTRESS_MODE: unsupported value; use demo or local")
    return mode
