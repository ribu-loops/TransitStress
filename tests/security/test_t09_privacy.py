"""Dashboard controller privacy/mode bounds and inert, self-contained exports."""

import asyncio
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from transitstress.artifacts import numeric_read, read_results_csv
from transitstress.errors import DataValidationError
from transitstress.ui import controller
from transitstress.ui.evidence import evidence_zip

ROOT = Path(__file__).resolve().parents[2]
LOOPS = [asyncio.new_event_loop() for _ in range(5)]


@pytest.fixture(scope="module", autouse=True)
def close_loops():
    yield
    for loop in LOOPS:
        loop.close()


def test_demo_direct_calls_deny_before_parser_or_core(monkeypatch):
    monkeypatch.delenv("TRANSITSTRESS_MODE", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("demo entered scientific/upload boundary")

    monkeypatch.setattr(controller, "parse_csv", forbidden)
    monkeypatch.setattr(controller, "single_case", forbidden)
    monkeypatch.setattr(controller, "load_split", forbidden)
    request = controller.Request("CSV", confirmed=True, csv_content=b"data")
    for call in (
        lambda: controller.accept_csv(b"data", True),
        lambda: controller.built_in_curve("8195886"),
        lambda: controller.prepare(request),
        lambda: controller.SessionJob().run(request),
        lambda: controller.SessionJob().export(),
    ):
        with pytest.raises(DataValidationError, match="demo denies"):
            call()


def test_query_and_session_mode_cannot_enable_local(monkeypatch):
    monkeypatch.delenv("TRANSITSTRESS_MODE", raising=False)
    monkeypatch.setattr(asyncio, "new_event_loop", LOOPS.pop)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.query_params.update(mode="local", path="../private.csv", run="true")
    at.session_state["mode"] = "local"
    at.session_state["confirmed"] = True
    at.run()
    assert not at.exception and not at.get("file_uploader")
    assert [b.label for b in at.button] == ["Reset to full baseline"]
    assert "Demo mode denies" in at.info[0].value


@pytest.mark.parametrize(
    "content,reason",
    [
        (b"x" * (5 * 1024**2 + 1), "bytes"),
        (b"time_days,flux,flux_err\n0,=1,1\n", "numeric"),
        (b"time_days,flux,flux_err\n0,NaN,1\n", "numeric"),
        (b"time_days,flux,flux_err\n0,1,0\n", "range"),
        (b"time_days,flux,flux_err\n0,1,1\n41,1,1\n", "baseline"),
        (b"time_days,flux,flux_err\n0,1,1\n24,1,1\n", "bins"),
    ],
    ids=["oversized", "formula", "nan", "nonpositive", "long-baseline", "sparse"],
)
def test_direct_csv_rejected_before_science(content, reason, monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")
    monkeypatch.setattr(
        controller, "single_case", lambda *a, **k: pytest.fail("invalid entered core")
    )
    with pytest.raises(DataValidationError, match=reason):
        controller.SessionJob().run(controller.Request("CSV", csv_content=content, confirmed=True))


@pytest.mark.parametrize(
    "changes",
    [
        dict(period_days=float("nan")),
        dict(period_days=9),
        dict(epoch_fraction=1),
        dict(depth_ppm=0),
        dict(mask_name="../../../x"),
        dict(source="https://example.org/x.csv"),
        dict(confirmed=False),
    ],
)
def test_direct_settings_rejected(changes, monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")
    content = (
        "time_days,flux,flux_err\n" + "".join(f"{i / 100},1,.001\n" for i in range(2401))
    ).encode()
    request = controller.Request("CSV", csv_content=content, confirmed=True)
    monkeypatch.setattr(
        controller, "single_case", lambda *a, **k: pytest.fail("invalid entered core")
    )
    with pytest.raises(DataValidationError):
        controller.SessionJob().run(replace(request, **changes))


def test_private_content_in_memory_export_roundtrip_no_cache(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("TRANSITSTRESS_MODE", "local")
    monkeypatch.setenv("T09_PRIVATE_CANARY", "private-environment-canary-t09")
    marker = "</script><img src=x onerror=alert(1)>"
    content = (
        "time_days,flux,flux_err,notes\n"
        + "".join(f"{i / 100},1,.001,{marker}\n" for i in range(2401))
    ).encode()
    request = controller.Request("CSV", csv_content=content, confirmed=True, period_days=2.73)
    from transitstress import acquisition, workbench

    def forbidden(*args, **kwargs):
        pytest.fail("private in-memory run attempted persistence")

    monkeypatch.setattr(acquisition, "atomic_write", forbidden)
    monkeypatch.setattr(workbench, "numeric_write", forbidden)
    a, b = controller.SessionJob(), controller.SessionJob()
    assert a.run(request)
    assert b.result is None and b.request is None
    assert all(r["status"] == "ok" for r in a.result["rows"])
    data = a.export()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {
            "results.csv",
            "results.json",
            "settings.json",
            "provenance.json",
            "injected_arrays.npz",
            "background_arrays.npz",
            "report.html",
            "checksums.json",
        }
        for name, expected in json.loads(archive.read("checksums.json")).items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
        settings = json.loads(archive.read("settings.json"))
        assert settings["requested"]["period_days"] == 2.73
        assert settings["units"]["absolute_epoch"] is None
        assert settings["source_csv_sha256"] == hashlib.sha256(content).hexdigest()
        assert settings["notices"] == [
            "Ignored 1 extra columns; contents are not retained or exported."
        ]
        json_rows = json.loads(archive.read("results.json"))["rows"]
        assert json_rows == json.loads(json.dumps(a.result["rows"], allow_nan=False))
        csv_rows = read_results_csv(archive.read("results.csv"))
        for row, original in zip(csv_rows, a.result["rows"], strict=True):
            assert row["period_days"] == original["period_days"]
        for name, record in settings["numerical"].items():
            (tmp_path / name).write_bytes(archive.read(name))
            decoded = numeric_read(tmp_path, name, record)
            kind = "injection" if name.startswith("injected") else "uninjected_real"
            cid = next(r["case_id"] for r in a.result["rows"] if r["case_kind"] == kind)
            for key, values in decoded.items():
                np.testing.assert_array_equal(values, a.result["details"][cid][key])
        report = archive.read("report.html").decode()
        assert 'src="https:' not in report and 'src="http:' not in report
        assert 'id="evidence-plot-2"' in report
        text = "\n".join(archive.read(n).decode(errors="ignore") for n in archive.namelist())
        assert marker not in text and "private-environment-canary-t09" not in text
        assert str(ROOT) not in text and "C:\\Users" not in text
    assert marker not in caplog.text and "private-environment-canary-t09" not in caplog.text
    assert request.fingerprint() != replace(request, depth_ppm=1000).fingerprint()
    assert (
        request.fingerprint()
        != replace(request, csv_content=content.replace(b",1,", b",1.01,")).fingerprint()
    )
    assert b.evidence is None
    corrupt = dict(a.result, run_id="0" * 64)
    with pytest.raises(DataValidationError, match="identity"):
        evidence_zip(corrupt, request, request.fingerprint())
