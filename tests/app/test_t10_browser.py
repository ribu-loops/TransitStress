"""Current app entry, honest filter denominators and navigation without science."""

import asyncio
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from transitstress.ui import bundle

ROOT = Path(__file__).resolve().parents[2]
LOOPS = [asyncio.new_event_loop() for _ in range(40)]


@pytest.fixture(autouse=True)
def runtime(monkeypatch, offline):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "demo")
    monkeypatch.setattr(asyncio, "new_event_loop", LOOPS.pop)
    for name in (
        "transitstress.search.search_bls",
        "transitstress.ui.controller.single_case",
        "transitstress.ui.controller.load_split",
    ):
        monkeypatch.setattr(name, lambda *a, **k: pytest.fail("demo invoked science/cache"))


@pytest.fixture(scope="module", autouse=True)
def close_loops():
    yield
    for loop in LOOPS:
        loop.close()


def app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=45).run()


def test_four_destinations_known_signal_and_offline_demo():
    at = app()
    assert not at.exception
    assert at.sidebar.radio[0].options == ["Start", "Workbench", "Benchmark", "Methods"]
    assert [m.value for m in at.metric] == ["79/135", "74/135"]
    assert any("Pi Mensae" in s.value for s in at.subheader)
    at.sidebar.radio[0].set_value("Workbench").run()
    assert not at.exception and not at.get("file_uploader")
    assert not any(b.label == "Run comparison" for b in at.button)
    for cid in at.selectbox[0].options:
        # AppTest options contain formatted labels; use underlying bundle IDs.
        assert len(cid) > 12
    ids = [c["case_id"] for c in bundle.load_bundle().manifest["curated"]]
    for cid in ids:
        at.selectbox[0].set_value(cid).run()
        assert not at.exception
        assert cid in " ".join(t.value for t in at.text)
        assert len(at.get("download_button")) == 1
    original = [m.value for m in at.metric]
    at.slider[0].set_value((3.0, 4.0)).run()
    at.radio[1].set_value("Overlay").run()
    assert not at.exception and [m.value for m in at.metric] == original
    next(b for b in at.button if b.label == "Reset to full baseline").click().run()
    assert at.slider[0].value[0] == 0
    at.sidebar.radio[0].set_value("Methods").run()
    assert not at.exception and any("G3" in m.value for m in at.markdown)
    assert any("750 ppm" in m.value for m in at.markdown)
    at.sidebar.radio[0].set_value("Workbench").run()
    assert not at.exception and isinstance(at.slider[0].value, tuple)
    assert len(at.get("download_button")) == 1


def test_filters_denominators_all_outcomes_and_summary_only():
    at = app()
    at.sidebar.radio[0].set_value("Benchmark").run()
    assert not at.exception and [m.value for m in at.metric] == ["79/135", "74/135"]
    assert any("390 / 390" in m.value for m in at.markdown)
    assert any("Summary-only row" in i.value for i in at.info)
    at.selectbox[0].set_value("24231627").run()
    assert [m.value for m in at.metric] == ["9/27", "3/27"]
    at.selectbox[2].set_value(2250).run()
    assert [m.value for m in at.metric] == ["9/9", "3/9"]
    at.selectbox[1].set_value("central_block").run()
    assert [m.value for m in at.metric] == ["3/3", "1/3"]
    at.selectbox[5].set_value("exact").run()
    assert [m.value for m in at.metric] == ["3/3", "1/3"]
    assert any("Showing 4 / 6 selected rows" in m.value for m in at.markdown)
    at.selectbox[6].set_value("search_error").run()
    assert any("No rows match" in i.value for i in at.info)
    assert not at.exception and not at.get("download_button")[0].label.startswith(
        "Download filtered"
    )


def test_missing_full_bundle_fails_at_start_before_any_result(monkeypatch, tmp_path):
    monkeypatch.setattr(bundle, "BUNDLE", tmp_path)
    at = app()
    assert not at.exception and "Restore demo/t10" in at.error[0].value
    assert not at.metric


def test_demo_ignores_session_and_query_mode_and_has_no_private_job():
    at = app()
    at.session_state["mode"] = "local"
    at.query_params["mode"] = "local"
    at.query_params["path"] = "runs/private"
    at.sidebar.radio[0].set_value("Workbench").run()
    assert not at.exception and not at.get("file_uploader")
    assert any("Demo mode denies" in m.value for m in at.info)
    assert "job" not in at.session_state
