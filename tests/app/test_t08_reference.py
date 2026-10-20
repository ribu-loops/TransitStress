"""Offline UI/component contracts for the bounded T08 reference, not T09 flows."""

import asyncio
import hashlib
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from transitstress.ui import reference
from transitstress.ui.charts import folded, gap_line, periodogram, timeline

ROOT = Path(__file__).resolve().parents[2]
# Windows event-loop creation needs a local socketpair. Allocate at collection,
# before the offline guard; application execution retains every network block.
LOOPS = [asyncio.new_event_loop() for _ in range(5)]


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
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert at.subheader[0].value == "Precomputed development result"
    assert not at.button and not at.file_uploader
    assert "79/135" in " ".join(m.value for m in at.markdown)
    assert "five evaluation stars" in at.warning[0].value
    for view in ["Fitted trends", "Processed flux"]:
        at.radio[0].set_value(view).run()
        assert not at.exception
        assert [m.value for m in at.metric] == ["1.726092", "6.654480"]


def test_missing_artifact_has_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(reference, "BUNDLE", tmp_path)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert "missing or corrupt" in at.error[0].value
    assert not at.metric


def test_corrupt_numeric_transport_is_rejected(monkeypatch, tmp_path):
    (tmp_path / "case.json").write_bytes((reference.BUNDLE / "case.json").read_bytes())
    (tmp_path / "case.npz").write_bytes(b"invalid numeric payload")
    monkeypatch.setattr(reference, "BUNDLE", tmp_path)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
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
    assert [trace.name for trace in fig.data] == ["A / 0.5 d", "B / 1.5 d"]
    assert [trace.line.dash for trace in fig.data] == ["solid", "dash"]
    assert all(None in trace.x and not trace.connectgaps for trace in fig.data)
    assert fig.layout.yaxis.title.text == "Normalized flux (dimensionless)"
    assert "yaxis2" not in fig.layout  # one shared y scale
    assert list(fig.layout.xaxis.range) == meta["rows"][0]["original_bounds_days"]
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
