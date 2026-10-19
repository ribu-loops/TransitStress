"""Every schema field is challenged, not only representative search bounds."""

import copy
import tomllib
from dataclasses import FrozenInstanceError, asdict, fields
from pathlib import Path

import pytest

from transitstress.config import SearchConfig, load_config, validate_config
from transitstress.errors import ConfigurationError
from transitstress.provenance import canonical_hash

RAW = tomllib.loads(Path("configs/protocol_v1.toml").read_text(encoding="utf-8"))


def leaves(value, path=()):
    for key, item in value.items():
        location = (*path, key)
        if isinstance(item, dict):
            yield from leaves(item, location)
        elif key == "families":
            for index, family in enumerate(item):
                yield from leaves(family, (*location, index))
        else:
            yield location, item


LEAVES = list(leaves(RAW))


def assign(raw, path, value):
    parent = raw
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value


@pytest.mark.parametrize("path,value", LEAVES, ids=lambda item: str(item))
def test_every_field_rejects_wrong_type(path, value):
    raw = copy.deepcopy(RAW)
    assign(raw, path, None)
    with pytest.raises(ConfigurationError):
        validate_config(raw)


@pytest.mark.parametrize("path,value", LEAVES, ids=lambda item: str(item))
def test_every_field_is_required(path, value):
    raw = copy.deepcopy(RAW)
    parent = raw
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(ConfigurationError, match="missing keys"):
        validate_config(raw)


@pytest.mark.parametrize("section", [None, *[k for k, v in RAW.items() if isinstance(v, dict)]])
def test_unknown_keys_at_each_table(section):
    raw = copy.deepcopy(RAW)
    table = raw if section is None else raw[section]
    table["unexpected_truth"] = 3.41
    with pytest.raises(ConfigurationError, match="unknown keys"):
        validate_config(raw)


@pytest.mark.parametrize("path,value", LEAVES, ids=lambda item: str(item))
def test_no_silent_scientific_protocol_changes(path, value):
    if path in [("master_seed",), ("frozen",)]:
        return  # provenance seed and freeze marker have explicit separate validation
    raw = copy.deepcopy(RAW)
    changed = (
        not value
        if type(value) is bool
        else (
            value + 1
            if type(value) in (int, float)
            else ("unsupported" if isinstance(value, str) else [])
        )
    )
    assign(raw, path, changed)
    with pytest.raises(ConfigurationError):
        validate_config(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, 10**1000])
def test_bad_numeric_settings_are_controlled(value):
    raw = copy.deepcopy(RAW)
    raw["search"]["minimum_period_days"] = value
    with pytest.raises(ConfigurationError):
        validate_config(raw)


@pytest.mark.parametrize("value", [-1, 2**64, True, 1.5])
def test_seed_range_and_type(value):
    raw = copy.deepcopy(RAW)
    raw["master_seed"] = value
    with pytest.raises(ConfigurationError):
        validate_config(raw)


def test_typed_config_immutable_roundtrip_and_identity():
    config = load_config(Path("configs/protocol_v1.toml"))
    assert validate_config(asdict(config)) == config
    assert config.search.duration_days == (1 / 24, 2 / 24, 3 / 24, 4 / 24, 6 / 24)
    with pytest.raises(FrozenInstanceError):
        config.search.oversample = 9
    assert not any("truth" in field.name or "epoch" in field.name for field in fields(SearchConfig))
    raw = copy.deepcopy(RAW)
    raw["master_seed"] = 0
    assert canonical_hash(validate_config(raw)) != canonical_hash(config)


def test_family_extra_key_and_direct_config_validation():
    raw = copy.deepcopy(RAW)
    raw["injection"]["families"][0]["truth_mask"] = [True]
    with pytest.raises(ConfigurationError, match="unknown keys"):
        validate_config(raw)
    with pytest.raises(ConfigurationError):
        SearchConfig(frequency_factor=-1)


def test_malformed_toml_is_controlled(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_text("[search", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="check the file"):
        load_config(path)


def test_invalid_utf8_and_direct_nonstring_keys_are_controlled(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigurationError):
        load_config(path)
    with pytest.raises(ConfigurationError):
        validate_config({1: 1, "other": 2})
