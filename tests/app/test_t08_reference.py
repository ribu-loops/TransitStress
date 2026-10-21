"""Offline UI/component contracts for the bounded T08 reference, not T09 flows."""

import asyncio
import hashlib
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from transitstress.ui import reference
from transitstress.ui.charts import (
    PIPELINES,
    baseline_context,
    folded,
    gap_line,
    periodogram,
    timeline,
)

ROOT = Path(__file__).resolve().parents[2]
# Windows event-loop creation needs a local socketpair. Allocate at collection,
# before the offline guard; application execution retains every network block.
LOOPS = [asyncio.new_event_loop() for _ in range(10)]


@pytest.fixture(autouse=True)
def app_loops(monkeypatch, offline):
    monkeypatch.setattr(asyncio, "new_event_loop", LOOPS.pop)
    yield


@pytest.fixture(scope="module", autouse=True)
def close_unused_loops():
    yield
    for loop in LOOPS:
        loop.close()


def test_reference_is_read_only_and_changes_only_presentation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reference must not run science")

    monkeypatch.setattr("transitstress.search.search_bls", forbidden)
    at = AppTest.from_string(
        "from transitstress.ui.reference import main\nmain()", default_timeout=30
    ).run()
    assert not at.exception
    assert at.subheader[0].value == "Precomputed development result"
    assert [b.label for b in at.button] == ["Reset to full baseline"]
    assert not at.file_uploader
    assert at.radio[0].value == "Processed flux"
    assert at.radio[1].value == "Stacked A/B"
    assert "79/135" in " ".join(m.value for m in at.markdown)
    assert "five evaluation stars" in at.warning[0].value
    for view in ["Fitted trends", "Prepared & injected"]:
        at.radio[0].set_value(view).run()
        assert not at.exception
        assert [m.value for m in at.metric] == ["1.726092", "6.654480"]


def test_display_zoom_overlay_and_reset_never_search(monkeypatch, caplog):
    def forbidden(*args, **kwargs):
        raise AssertionError("Display controls must not run science")

    monkeypatch.setattr("transitstress.search.search_bls", forbidden)
    at = AppTest.from_string(
        "from transitstress.ui.reference import main\nmain()", default_timeout=30
    ).run()
    baseline = at.slider[0].value
    at.slider[0].set_value((1.5, 2.5)).run()
    assert not at.exception
    assert "Displayed time range: 1.50–2.50 relative days" in [m.value for m in at.markdown]
    assert [m.value for m in at.metric] == ["1.726092", "6.654480"]
    at.radio[1].set_value("Overlay").run()
    assert not at.exception
    assert at.slider[0].value == (1.5, 2.5)
    at.button[0].click().run()
    assert not at.exception
    assert at.slider[0].value == baseline
    assert "created with a default value" not in caplog.text
    at.slider[0].set_value((2.0, 2.0)).run()
    assert not at.exception
    assert "choose an end later than the start" in at.warning[0].value


def test_missing_artifact_has_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(reference, "BUNDLE", tmp_path)
    at = AppTest.from_string(
        "from transitstress.ui.reference import main\nmain()", default_timeout=30
    ).run()
    assert not at.exception
    assert "missing or corrupt" in at.error[0].value
    assert not at.metric


def test_corrupt_numeric_transport_is_rejected(monkeypatch, tmp_path):
    (tmp_path / "case.json").write_bytes((reference.BUNDLE / "case.json").read_bytes())
    (tmp_path / "case.npz").write_bytes(b"invalid numeric payload")
    monkeypatch.setattr(reference, "BUNDLE", tmp_path)
    at = AppTest.from_string(
        "from transitstress.ui.reference import main\nmain()", default_timeout=30
    ).run()
    assert not at.exception
    assert "Restore demo/t08" in at.error[0].value


def test_gap_display_and_chart_contracts():
    t = np.array([0.0, 0.1, 1.0, 1.1])
    y = np.array([1.0, 0.9, 0.8, 1.0])
    x, displayed = gap_line(t, y)
    assert x == [0.0, 0.1, None, 1.0, 1.1]
    assert displayed == [1.0, 0.9, None, 0.8, 1.0]
    np.testing.assert_array_equal(y, [1.0, 0.9, 0.8, 1.0])
    meta, arrays = reference.load_example()
    before = {k: a.copy() for k, a in arrays.items()}
    fig = timeline(arrays, "Processed flux", meta["rows"][0]["original_bounds_days"])
    assert [trace.name for trace in fig.data] == [p[1] for p in PIPELINES]
    assert all(trace.mode == "markers" and len(trace.x) == 3104 for trace in fig.data)
    assert all(trace.marker.size == 3 and trace.marker.opacity == 0.45 for trace in fig.data)
    assert fig.data[0].marker.symbol != fig.data[1].marker.symbol
    assert fig.layout.yaxis.title.text == "Normalized flux (dimensionless)"
    assert list(fig.layout.yaxis.range) == list(fig.layout.yaxis2.range)
    assert fig.layout.yaxis2.matches == "y"
    assert fig.layout.yaxis.fixedrange and fig.layout.yaxis2.fixedrange
    assert list(fig.layout.xaxis.range) == meta["rows"][0]["original_bounds_days"]
    assert list(fig.layout.xaxis.range) == list(fig.layout.xaxis2.range)
    for i, pipe in enumerate(("rm_0p5d", "rm_1p5d")):
        np.testing.assert_array_equal(fig.data[i].x, arrays["time_days"])
        np.testing.assert_array_equal(fig.data[i].y, arrays[f"{pipe}_flux"])
        assert min(fig.layout.yaxis.range) < arrays[f"{pipe}_flux"].min()
        assert max(fig.layout.yaxis.range) > arrays[f"{pipe}_flux"].max()
    zoom = timeline(arrays, "Processed flux", [1.5, 2.5])
    overlay = timeline(arrays, "Processed flux", [1.5, 2.5], "Overlay")
    assert list(zoom.layout.xaxis2.range) == [1.5, 2.5]
    assert list(zoom.layout.yaxis.range) == list(fig.layout.yaxis.range)
    assert list(overlay.layout.yaxis.range) == list(fig.layout.yaxis.range)
    assert all(len(trace.x) == 3104 for trace in zoom.data + overlay.data)
    assert "yaxis2" not in overlay.layout
    overview = baseline_context(arrays, meta["rows"][0]["original_bounds_days"], (1.5, 2.5))
    assert list(overview.layout.xaxis.range) == meta["rows"][0]["original_bounds_days"]
    np.testing.assert_array_equal(overview.data[0].y, arrays["input_flux"])
    assert overview.layout.shapes[0].x0 == 1.5
    assert overview.layout.shapes[0].x1 == 2.5
    trends = timeline(arrays, "Fitted trends", [0, 27.77])
    assert all(None in trace.x and not trace.connectgaps for trace in trends.data[1:])
    bls = periodogram(arrays, 1.73)
    assert list(bls.layout.xaxis.range) == [0.75, 10]
    assert bls.layout.yaxis.title.text == "BLS likelihood power"
    fold = folded(arrays, meta["rows"])
    assert all(trace.mode == "markers" and len(trace.x) == 3104 for trace in fold.data)
    for key, value in before.items():
        np.testing.assert_array_equal(arrays[key], value)


def test_split_heading_corrects_original_generic_label():
    assert reference.presentation_heading("evaluation") == "Precomputed evaluation result"
    with pytest.raises(ValueError, match="split"):
        reference.presentation_heading("<script>")
    meta, _ = reference.load_example()
    assert meta["split"] == "development"
    assert (
        meta["case"]["case_id"]
        == "6bd4e804ffa7f720c4056a695b0b5a5e5db445e75778af0eb220bcee886af09e"
    )
    assert (
        hashlib.sha256((reference.BUNDLE / "case.npz").read_bytes()).hexdigest()
        == meta["numerical"]["transport_sha256"]
    )
