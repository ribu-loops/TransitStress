import importlib
from pathlib import Path

import pytest

from transitstress.cli import DEPENDENCIES, main
from transitstress.config import ConfigurationError, app_mode, load_config

PROTOCOL = Path("configs/protocol_v1.toml")


def test_locked_runtime_imports():
    for name in DEPENDENCIES:
        importlib.import_module(name)


def test_protocol_smoke():
    config = load_config(PROTOCOL)
    assert config.protocol_id == "transitstress-v1-cohort1"
    assert (config.minimum_period_days, config.maximum_period_days) == (0.75, 10.0)


@pytest.mark.parametrize(
    "old,new",
    [
        ("minimum_period_days = 0.75", "minimum_period_days = 12.0"),
        ("frequency_factor = 1.0", "frequency_factor = nan"),
        ("workers = 1", "workers = true"),
        ("schema_version = 2", "schema_version = 2\nunknown = 5"),
    ],
)
def test_invalid_config(tmp_path, old, new):
    path = tmp_path / "invalid.toml"
    path.write_text(PROTOCOL.read_text().replace(old, new), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_doctor_missing_config_nonzero(tmp_path, capsys):
    assert main(["doctor", "--config", str(tmp_path / "absent.toml")]) == 2
    assert "check the file" in capsys.readouterr().err


def test_default_mode(monkeypatch):
    monkeypatch.delenv("TRANSITSTRESS_MODE", raising=False)
    assert app_mode() == "demo"


def test_unknown_mode(monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "invalid")
    with pytest.raises(ConfigurationError, match="use demo or local"):
        app_mode()
