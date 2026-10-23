"""Saved evidence, screen styling and route state; no scientific producer is executed."""

import asyncio
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from transitstress.ui import browser, controller, local
from transitstress.ui.bundle import case_zip, load_bundle
from transitstress.ui.charts import timeline
from transitstress.ui.presentation import BACKGROUND, TEXT, YELLOW, dark_figure

ROOT = Path(__file__).resolve().parents[2]
LOOPS = [asyncio.new_event_loop() for _ in range(100)]


@pytest.fixture(autouse=True)
def guarded(monkeypatch, offline):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "demo")
    monkeypatch.setattr(asyncio, "new_event_loop", LOOPS.pop)

    def forbidden(*a, **k):
        pytest.fail("presentation invoked preparation/science/acquisition")

    for target in (
        "transitstress.search.search_bls",
        "transitstress.ui.controller.single_case",
        "transitstress.ui.controller.load_split",
    ):
        monkeypatch.setattr(target, forbidden)
    monkeypatch.setattr(local, "prepare", forbidden)
    yield


@pytest.fixture(scope="module", autouse=True)
def close_loops():
    yield
    for loop in LOOPS:
        loop.close()


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_primary_route_guide_return_and_pair_binding():
    bundle = load_bundle()
    cid = next(c["case_id"] for c in bundle.manifest["curated"] if c["stem"] == "example3")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    assert not at.exception and not at.metric
    button(at, "Explore a worked example").click().run()
    assert not at.exception
    assert at.selectbox(key="curated_case_widget").value == cid
    assert at.radio(key="reading_choice").value == browser.STEPS[0]
    at.radio(key="reading_choice").set_value(browser.STEPS[2]).run()
    assert not at.exception
    at.slider(key="public_time_range").set_value((3.0, 4.0)).run()
    at.radio(key="public_layout").set_value("Overlay").run()
    at.radio(key="reading_choice").set_value(browser.STEPS[3]).run()
    rows = [r for r in bundle.rows if r["case_id"] == cid]
    assert [m.value for m in at.metric] == [f"{r['period_days']:.6f}" for r in rows]
    at.sidebar.radio[0].set_value("Methods").run()
    at.sidebar.radio[0].set_value("Workbench").run()
    assert not at.exception
    assert at.radio(key="reading_choice").value == browser.STEPS[3]
    at.radio(key="reading_choice").set_value(browser.STEPS[2]).run()
    assert at.slider(key="public_time_range").value == (3.0, 4.0)
    assert at.radio(key="public_layout").value == "Overlay"
    button(at, "Reset to full baseline").click().run()
    assert at.slider(key="public_time_range").value == tuple(rows[0]["original_bounds_days"])
    at.radio(key="reading_choice").set_value(browser.STEPS[4]).run()
    assert len(at.get("download_button")) == 1
    with zipfile.ZipFile(io.BytesIO(case_zip(bundle, cid))) as archive:
        assert json.loads(archive.read("results.json"))["rows"] == rows
        assert archive.read("arrays.npz") == bundle.content["example3.npz"]
    assert not at.exception and "job" not in at.session_state


def test_filters_survive_destination_return_without_changing_denominator():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    button(at, "Browse benchmark").click().run()
    at.selectbox(key="filter_star").set_value("24231627").run()
    at.selectbox(key="filter_depth").set_value(2250).run()
    at.selectbox(key="filter_mask").set_value("central_block").run()
    assert [m.value for m in at.metric] == ["3/3", "1/3"]
    at.selectbox(key="filter_outcome").set_value("exact").run()
    at.sidebar.radio[0].set_value("Methods").run()
    at.sidebar.radio[0].set_value("Benchmark").run()
    assert not at.exception
    assert at.selectbox(key="filter_star").value == "24231627"
    assert at.selectbox(key="filter_outcome").value == "exact"
    assert [m.value for m in at.metric] == ["3/3", "1/3"]
    assert any("Showing 4 / 6 selected rows" in m.value for m in at.markdown)


def test_local_unsubmitted_csv_and_inputs_survive_new_primary_route(monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    at.sidebar.radio[0].set_value("Workbench").run()
    at.selectbox(key="source").set_value("CSV").run()
    private = b"time_days,flux,flux_err\n0,1,.01\n"
    # Session boundary injection only. No parser/preparation is allowed in this test.
    at.session_state["local_draft"]["csv_content"] = private
    at.number_input(key="period").set_value(4.25).run()
    at.number_input(key="depth").set_value(1000.0).run()
    job = at.session_state["job"]
    at.sidebar.radio[0].set_value("Start").run()
    button(at, "Explore a worked example").click().run()
    assert at.sidebar.radio[1].value == "Curated examples"
    at.sidebar.radio[1].set_value("Local experiment").run()
    assert not at.exception
    assert at.session_state["job"] is job and job.result is None
    assert at.session_state["local_draft"]["csv_content"] == private
    assert at.number_input(key="period").value == 4.25
    assert at.number_input(key="depth").value == 1000
    assert not at.checkbox(key="confirmed").value
    assert button(at, "Run comparison").disabled
    other = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    other.sidebar.radio[0].set_value("Workbench").run()
    assert other.session_state["job"] is not job
    assert other.session_state["local_draft"]["csv_content"] is None
    assert other.number_input(key="period").value == 3.41
    job.lock.acquire()
    try:
        with pytest.raises(controller.DataValidationError, match="job busy"):
            job.export()
    finally:
        job.lock.release()
    button(at, "Reset workbench").click().run()
    assert at.session_state["local_draft"]["csv_content"] is None


def test_screen_adapter_preserves_arrays_axes_gaps_and_historical_style():
    bundle = load_bundle()
    cid = next(iter(bundle.arrays))
    arrays = bundle.arrays[cid]
    row = next(r for r in bundle.rows if r["case_id"] == cid)
    for view in ("Processed flux", "Prepared & injected", "Fitted trends"):
        original = timeline(arrays, view, row["original_bounds_days"])
        before = original.to_json()
        screen = dark_figure(original)
        assert json.loads(original.to_json()) == json.loads(before)
        assert screen.layout.paper_bgcolor == BACKGROUND
        assert screen.layout.font.color == TEXT
        for old, new in zip(original.data, screen.data, strict=True):
            np.testing.assert_array_equal(new.x, old.x)
            np.testing.assert_array_equal(new.y, old.y)
        assert screen.layout.xaxis.range == original.layout.xaxis.range
        assert screen.layout.yaxis.range == original.layout.yaxis.range
    assert dark_figure(original).data[-1].line.color == YELLOW


def test_local_completed_state_stale_failure_and_export_lock_with_saved_rows(monkeypatch):
    """UI-only saved-state fixture; no claim that these benchmark rows were a local run."""
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")
    bundle = load_bundle()
    cid = next(iter(bundle.arrays))
    rows = [r for r in bundle.rows if r["case_id"] == cid]
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    at.sidebar.radio[0].set_value("Workbench").run()
    job = at.session_state["job"]
    request = controller.Request("8195886")
    job.request, job.fingerprint = request, request.fingerprint()
    job.result = dict(
        rows=rows,
        run_id="saved-ui-fixture",
        notices=[],
        details={cid: bundle.arrays[cid]},
        identity=bundle.identity,
        provenance={"fixture": "saved rows for UI state only"},
    )
    job.elapsed_seconds = 0
    job.evidence = case_zip(bundle, cid)
    evidence = job.evidence
    at.run()
    assert not at.exception and any("Result current" in s.value for s in at.success)
    at.number_input(key="period").set_value(4.25).run()
    assert any("Result stale" in w.value for w in at.warning)
    for route in ("Start", "Benchmark", "Methods"):
        at.sidebar.radio[0].set_value(route).run()
        at.sidebar.radio[0].set_value("Workbench").run()
        assert not at.exception
        assert job.export() == evidence
        assert at.number_input(key="period").value == 4.25
        assert any("Result stale" in w.value for w in at.warning)
    # Mock readiness and an expected producer failure; no science is permitted.
    monkeypatch.setattr(
        local,
        "prepare",
        lambda request: (_ for _ in ()).throw(
            controller.DataValidationError("input fixture: unavailable; retain prior result")
        ),
    )
    at.checkbox(key="confirmed").check().run()
    assert any("input fixture" in e.value for e in at.error)
    assert job.export() == evidence and not job.running


def test_explicit_run_expected_failure_uses_mock_only(monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")

    @dataclass
    class Units:
        label: str = "mock readiness only"

    curve = SimpleNamespace(
        time_days=range(2000),
        original_start_days=0,
        original_end_days=27,
        curve_id="mock-ready",
        content_hash="0" * 64,
        time_metadata=Units(),
    )
    monkeypatch.setattr(local, "prepare", lambda request: (curve, ()))
    calls = []

    def fail(job, request):
        calls.append(request)
        raise controller.DataValidationError("mock search failure: retain input; inspect evidence")

    monkeypatch.setattr(controller.SessionJob, "run", fail)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    at.sidebar.radio[0].set_value("Workbench").run()
    at.checkbox(key="confirmed").check().run()
    at.number_input(key="period").set_value(4.25).run()
    assert not calls and not at.exception
    button(at, "Run comparison").click().run()
    assert len(calls) == 1 and calls[0].period_days == 4.25
    assert not at.exception and any("mock search failure" in e.value for e in at.error)
    assert at.session_state["job"].result is None
    assert not at.get("download_button")
    assert not at.session_state["job"].running
