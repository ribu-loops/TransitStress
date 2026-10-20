"""T08 presentation mode/privacy/native-theme regressions."""

import asyncio
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
# Allocate only test-runtime local socketpairs before the default offline guard.
# No network method is unblocked while application code runs.
LOOPS = [asyncio.new_event_loop() for _ in range(3)]


@pytest.fixture(autouse=True)
def app_loops(monkeypatch, offline):
    monkeypatch.setattr(asyncio, "new_event_loop", LOOPS.pop)
    yield


@pytest.fixture(scope="module", autouse=True)
def close_unused_loops():
    yield
    for loop in LOOPS:
        loop.close()


@pytest.mark.parametrize("mode", ["demo", "local"])
def test_reference_has_no_input_or_execution_surface(mode, monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", mode)
    monkeypatch.setenv("T08_PRIVATE_CANARY", "private-canary-t08")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert not at.button and not at.file_uploader
    text = " ".join(m.value for m in at.markdown)
    assert "private-canary-t08" not in text
    assert "Precomputed development example" in at.info[0].value


def test_unknown_mode_fails_before_loading_example(monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "unsupported")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert "App mode is invalid" in at.error[0].value
    assert not at.metric


def test_native_theme_retains_security_defaults():
    settings = tomllib.loads((ROOT / ".streamlit/config.toml").read_text())
    assert settings["server"] == dict(enableCORS=True, enableXsrfProtection=True, maxUploadSize=5)
    assert settings["browser"]["gatherUsageStats"] is False
    assert settings["client"]["showErrorDetails"] == "none"
    assert settings["theme"]["textColor"] == "#172033"
