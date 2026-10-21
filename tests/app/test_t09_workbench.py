"""Offline state transitions around the frozen core; browser QA is recorded separately."""

import asyncio
import copy
from dataclasses import replace
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from transitstress.config import load_config
from transitstress.csv_input import parse_csv
from transitstress.errors import DataValidationError
from transitstress.ui import controller, local
from transitstress.workbench import single_case

ROOT = Path(__file__).resolve().parents[2]
LOOPS = [asyncio.new_event_loop() for _ in range(90)]
CONTENT = (
    "time_days,flux,flux_err\n" + "".join(f"{i / 100},1,.001\n" for i in range(3001))
).encode()


@pytest.fixture(autouse=True)
def local_mode(monkeypatch, offline):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")
    monkeypatch.setattr(asyncio, "new_event_loop", LOOPS.pop)
    yield


@pytest.fixture(scope="module", autouse=True)
def close_loops():
    yield
    for loop in LOOPS:
        loop.close()


@pytest.fixture(scope="module")
def core_result():
    # Explicit synthetic test fixture, not a built-in TESS or evaluation result.
    config = load_config(ROOT / "configs/protocol_v1.toml")
    return single_case(
        config,
        repository=ROOT,
        csv_content=CONTENT,
        confirm_days_and_linear_units=True,
        period_days=3.41,
        depth_ppm=750,
        duration_hours=3,
        epoch_fraction=0.43,
    )


@pytest.fixture
def prepared(monkeypatch, core_result):
    config = load_config(ROOT / "configs/protocol_v1.toml")
    curve = parse_csv(CONTENT, confirm_days_and_linear_units=True, config=config).curve
    monkeypatch.setattr(controller, "built_in_curve", lambda tic: curve)
    calls = []

    def compute(*args, **kwargs):
        calls.append(kwargs)
        if any(
            kwargs[key] != value
            for key, value in dict(
                period_days=3.41,
                depth_ppm=750,
                duration_hours=3,
                epoch_fraction=0.43,
                mask_name="original",
            ).items()
        ):
            return single_case(
                args[0],
                repository=ROOT,
                csv_content=CONTENT,
                confirm_days_and_linear_units=True,
                **{
                    key: kwargs[key]
                    for key in (
                        "period_days",
                        "depth_ppm",
                        "duration_hours",
                        "epoch_fraction",
                        "mask_name",
                    )
                },
            )
        return copy.deepcopy(core_result)

    monkeypatch.setattr(controller, "single_case", compute)
    return calls


def app() -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=45).run()
    return at.sidebar.radio[0].set_value("Workbench").run()


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_ready_run_edit_stale_zoom_duplicate_and_reset(prepared):
    at = app()
    assert not at.exception and not at.metric
    assert button(at, "Run comparison").disabled
    at.checkbox[0].check().run()
    assert not button(at, "Run comparison").disabled
    assert not prepared
    button(at, "Run comparison").click().run()
    assert not at.exception and len(prepared) == 1
    assert len(at.metric) == 2
    job = at.session_state["job"]
    first_zip = job.export()
    assert len(at.get("download_button")) == 1
    at.number_input[1].set_value(1500.0).run()
    assert any("Result stale" in w.value for w in at.warning)
    assert "750 ppm" in at.text[1].value
    assert job.export() == first_zip and len(prepared) == 1
    at.slider[0].set_value((1.0, 2.0)).run()
    at.radio[1].set_value("Overlay").run()
    assert len(prepared) == 1 and not at.exception
    button(at, "Reset to full baseline").click().run()
    assert len(prepared) == 1
    at.number_input[1].set_value(750.0).run()
    assert any("Result current" in s.value for s in at.success)
    button(at, "Run comparison").click().run()
    assert len(prepared) == 1
    at.number_input[0].set_value(4.0).run()
    button(at, "Run comparison").click().run()
    assert len(prepared) == 2 and prepared[-1]["period_days"] == 4
    executed = at.session_state["job"].result
    at.sidebar.radio[0].set_value("Start").run()
    at.sidebar.radio[0].set_value("Workbench").run()
    assert not at.exception and isinstance(at.slider[0].value, tuple)
    assert at.session_state["job"].result is executed and len(prepared) == 2
    assert at.number_input[0].value == 4
    button(at, "Reset workbench").click().run()
    assert not at.metric and at.number_input[0].value == 3.41
    assert at.session_state["job"].result is None
    assert not at.checkbox[0].value


def test_two_ui_sessions_have_separate_jobs(prepared):
    a, b = app(), app()
    a.checkbox[0].check().run()
    button(a, "Run comparison").click().run()
    b.run()
    assert len(a.metric) == 2 and not b.metric
    assert a.session_state["job"] is not b.session_state["job"]
    assert b.session_state["job"].result is None


def test_unsubmitted_draft_survives_curated_and_destination_navigation(prepared, monkeypatch):
    at = app()
    at.checkbox[0].check().run()
    button(at, "Run comparison").click().run()
    job = at.session_state["job"]
    executed, evidence = job.result, job.export()
    at.number_input(key="period").set_value(4.25).run()
    at.number_input(key="depth").set_value(1000.0).run()
    at.number_input(key="duration").set_value(4.0).run()
    at.number_input(key="epoch").set_value(0.2).run()
    at.selectbox(key="mask").set_value("central_block").run()
    monkeypatch.setattr(
        controller, "single_case", lambda *a, **k: pytest.fail("navigation started science")
    )
    for route in ("Curated examples", "Methods", "Start"):
        if route == "Curated examples":
            at.sidebar.radio[1].set_value(route).run()
            at.sidebar.radio[1].set_value("Local experiment").run()
        else:
            at.sidebar.radio[0].set_value(route).run()
            at.sidebar.radio[0].set_value("Workbench").run()
        assert not at.exception
        assert [at.number_input(key=k).value for k in ("period", "depth", "duration", "epoch")] == [
            4.25,
            1000.0,
            4.0,
            0.2,
        ]
        assert at.selectbox(key="mask").value == "central_block"
        assert at.checkbox[0].value
        assert job.result is executed and job.export() == evidence
        assert any("Result stale" in w.value for w in at.warning)
    # A second session cannot inherit either draft or submitted private output.
    other = app()
    assert other.number_input(key="depth").value == 750
    assert other.session_state["job"].result is None
    button(at, "Reset workbench").click().run()
    assert not at.metric and not at.checkbox[0].value
    assert at.number_input(key="depth").value == 750


def test_csv_no_data_and_invalid_messages(monkeypatch):
    at = app()
    at.selectbox[0].set_value("CSV").run()
    at.checkbox[0].check().run()
    assert not at.exception and at.get("file_uploader")
    assert any("No data" in i.value for i in at.info)
    # AppTest lacks uploader byte injection. Drive the same controller boundary.
    with pytest.raises(DataValidationError, match="headers"):
        controller.accept_csv(b"time_days,flux,flux\n0,1,1\n", True)
    monkeypatch.setattr(
        local,
        "prepare",
        lambda r: (_ for _ in ()).throw(
            DataValidationError(
                "CSV headers: missing/duplicate; use unique time_days,flux,flux_err"
            )
        ),
    )
    at.selectbox[0].set_value("8195886").run()
    assert "CSV headers" in at.error[0].value and not at.exception
    assert button(at, "Run comparison").disabled


def test_csv_draft_survives_widget_expiry_and_explicit_clear(monkeypatch):
    monkeypatch.setattr(
        controller, "single_case", lambda *a, **k: pytest.fail("draft started science")
    )
    at = app()
    at.selectbox(key="source").set_value("CSV").run()
    # AppTest cannot inject uploader bytes; use its session-owned data boundary.
    at.session_state["local_draft"]["csv_content"] = CONTENT
    at.checkbox[0].check().run()
    assert not button(at, "Run comparison").disabled
    at.sidebar.radio[1].set_value("Curated examples").run()
    at.sidebar.radio[1].set_value("Local experiment").run()
    assert not at.exception
    assert at.selectbox(key="source").value == "CSV" and at.checkbox[0].value
    assert at.session_state["local_draft"]["csv_content"] == CONTENT
    assert not button(at, "Run comparison").disabled
    other = app()
    assert other.session_state["local_draft"]["csv_content"] is None
    button(at, "Clear CSV input").click().run()
    assert at.session_state["local_draft"]["csv_content"] is None
    assert button(at, "Run comparison").disabled
    assert at.session_state["job"].result is None


def test_expected_run_failure_retains_old_result(prepared, monkeypatch):
    at = app()
    at.checkbox[0].check().run()
    button(at, "Run comparison").click().run()
    old = at.session_state["job"].result
    monkeypatch.setattr(
        controller,
        "single_case",
        lambda *a, **k: (_ for _ in ()).throw(
            DataValidationError("search grid: invalid; restore fixed settings")
        ),
    )
    at.number_input[0].set_value(5.0).run()
    button(at, "Run comparison").click().run()
    assert not at.exception and "search grid" in at.error[0].value
    assert at.session_state["job"].result is old
    assert any("Result stale" in w.value for w in at.warning)
    assert not at.session_state["job"].running


def test_coverage_null_and_failure_rows_have_actionable_display(prepared, monkeypatch, core_result):
    result = copy.deepcopy(core_result)
    for row in result["rows"][2:]:
        row.update(
            status="search_error",
            period_days=None,
            coverage_eligible=None,
            observed_transits=None,
            full_centers_days=None,
            total_in_transit_points=None,
        )
        row["retained_depth"] = dict(value=None, reason="search_failure")
    monkeypatch.setattr(controller, "single_case", lambda *a, **k: result)
    at = app()
    at.checkbox[0].check().run()
    button(at, "Run comparison").click().run()
    assert not at.exception and any("Computation failed" in e.value for e in at.error)
    assert not at.get("plotly_chart")
    result = copy.deepcopy(core_result)
    for row in result["rows"][2:]:
        row["coverage_eligible"] = False
    monkeypatch.setattr(controller, "single_case", lambda *a, **k: result)
    button(at, "Reset workbench").click().run()
    at.checkbox[0].check().run()
    button(at, "Run comparison").click().run()
    assert not at.exception and any("Insufficient coverage" in w.value for w in at.warning)


def test_backend_duplicate_and_identity(prepared):
    job = controller.SessionJob()
    request = controller.Request("8195886", confirmed=True)
    job.lock.acquire()
    try:
        with pytest.raises(DataValidationError, match="already running"):
            job.run(request)
        with pytest.raises(DataValidationError, match="job busy"):
            job.export()
    finally:
        job.lock.release()
    assert not prepared
    assert job.run(request)
    assert not job.run(request)
    assert job.run(replace(request, depth_ppm=1000))
    assert len(prepared) == 2


def test_mismatched_core_result_cannot_replace_executed_identity(prepared, monkeypatch):
    job = controller.SessionJob()
    request = controller.Request("8195886", confirmed=True)
    job.run(request)
    old = job.result
    monkeypatch.setattr(controller, "single_case", lambda *a, **k: copy.deepcopy(old))
    with pytest.raises(DataValidationError, match="result identity"):
        job.run(replace(request, period_days=4))
    assert job.result is old and job.request == request and not job.running
