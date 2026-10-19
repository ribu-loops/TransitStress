"""Bootstrap validation only; complete scientific contracts arrive in T01."""

import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """An unsupported or malformed setting with a corrective message."""


@dataclass(frozen=True)
class BootstrapConfig:
    protocol_id: str
    frozen: bool
    minimum_period_days: float
    maximum_period_days: float


TOP_KEYS = {
    "schema_version",
    "protocol_id",
    "frozen",
    "master_seed",
    "data",
    "sanity",
    "preparation",
    "injection",
    "masks",
    "detrending",
    "search",
    "evaluation",
    "controls",
    "execution",
    "limits",
}


def app_mode() -> str:
    """Read only the explicitly allowed nonsecret setting."""
    mode = os.environ.get("TRANSITSTRESS_MODE", "demo")
    if mode not in {"demo", "local"}:
        raise ConfigurationError("TRANSITSTRESS_MODE: unsupported value; use demo or local")
    return mode


def _finite_values(value: object, field: str = "config") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigurationError(f"{field}: nonfinite number; supply a finite value")
    if isinstance(value, dict):
        for key, item in value.items():
            _finite_values(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite_values(item, f"{field}[{index}]")


def load_config(path: Path) -> BootstrapConfig:
    """Check bootstrap identity, finiteness and independent search bounds.

    This is not authorization to run any scientific operation or frozen evaluation.
    T01 must validate every nested scientific field before those APIs exist.
    """
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError("config: unreadable or invalid TOML; check the file") from error
    if raw.keys() - TOP_KEYS:
        raise ConfigurationError("config: unknown top-level key; use protocol_v1 schema")
    _finite_values(raw)
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ConfigurationError("schema_version: unsupported; use integer 1")
    if not isinstance(raw.get("protocol_id"), str) or not raw["protocol_id"].strip():
        raise ConfigurationError("protocol_id: missing identifier; supply a nonempty string")
    if type(raw.get("frozen")) is not bool:
        raise ConfigurationError("frozen: invalid type; supply a boolean")
    search = raw.get("search")
    if not isinstance(search, dict):
        raise ConfigurationError("search: missing table; supply independent search settings")
    bounds = [search.get(f"{name}_period_days") for name in ("minimum", "maximum")]
    if any(type(value) not in (int, float) for value in bounds):
        raise ConfigurationError("search period bounds: invalid type; supply finite day values")
    low, high = bounds
    if not 0 < low < high:
        raise ConfigurationError("search period bounds: require 0 < minimum < maximum; fix bounds")
    execution = raw.get("execution")
    if not isinstance(execution, dict) or type(execution.get("workers")) is not int:
        raise ConfigurationError("execution.workers: missing integer; use 1")
    if execution["workers"] != 1 or execution.get("allow_network_during_analysis") is not False:
        raise ConfigurationError("execution: unsupported workers/network; use 1 and false")
    return BootstrapConfig(raw["protocol_id"], raw["frozen"], float(low), float(high))
