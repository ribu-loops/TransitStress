"""BLS allocation/numerical bounds and truth-free direct API enforcement."""

import inspect
import io
from dataclasses import replace

import numpy as np
import pytest

from scripts.t04_arrays import load_arrays
from tests.unit.test_t04_science import curve
from transitstress import acquisition as acq
from transitstress.cli import main
from transitstress.config import SearchConfig
from transitstress.detrending import detrend
from transitstress.errors import DataValidationError
from transitstress.models import DetrendConfig
from transitstress.search import build_search_grid, search_bls


@pytest.mark.parametrize("baseline", [1000.0, 1e200])
def test_grid_bomb_rejected_before_library_allocation(monkeypatch, baseline):
    def forbidden(*a, **k):
        raise AssertionError("grid allocator must not run")

    monkeypatch.setattr("transitstress.search.BoxLeastSquares", forbidden)
    with pytest.raises(DataValidationError, match="50000 predicted"):
        build_search_grid(curve(original_end_days=baseline), SearchConfig())


def test_narrowed_grid_refused_before_power(monkeypatch):
    c = curve()
    grid = build_search_grid(c, SearchConfig())

    def forbidden(*a, **k):
        raise AssertionError("search must not run")

    monkeypatch.setattr("transitstress.search.BoxLeastSquares.power", forbidden)
    with pytest.raises(DataValidationError, match="full independent"):
        search_bls(
            c.time_days,
            c.flux,
            c.flux_err,
            replace(grid, periods_days=grid.periods_days[100:200]),
            SearchConfig(),
        )


@pytest.mark.parametrize("value", [np.nan, np.inf, 0.0, 1e-300, 1e155, 1e300])
def test_invalid_errors_fail_before_search(monkeypatch, value):
    c = curve()
    grid = build_search_grid(c, SearchConfig())

    def forbidden(*a, **k):
        raise AssertionError("search must not run")

    monkeypatch.setattr("transitstress.search.BoxLeastSquares.power", forbidden)
    with pytest.raises(DataValidationError):
        search_bls(c.time_days, c.flux, np.full(len(c.flux), value), grid, SearchConfig())


def test_detrend_numerical_failure_no_clamping():
    t = np.arange(101) / 100
    for f, e in [(np.full(101, -1.0), np.ones(101)), (np.ones(101), np.full(101, 1e-300))]:
        with pytest.raises(DataValidationError):
            detrend(t, f, e, DetrendConfig("rm_0p5d", 0.5))


def test_truth_not_accepted():
    assert tuple(inspect.signature(detrend).parameters) == (
        "time_days",
        "flux",
        "flux_err",
        "config",
    )
    assert tuple(inspect.signature(search_bls).parameters) == (
        "time_days",
        "detrended_flux",
        "detrended_error",
        "period_grid",
        "config",
    )


def test_sanity_cli_absent_manifest_nonzero(tmp_path, capsys):
    assert main(["sanity", "--manifest", str(tmp_path / "absent.json")]) == 2
    assert "sanity:" in capsys.readouterr().err


def test_numeric_reader_checks_size_before_read_or_numpy(tmp_path, monkeypatch):
    path = tmp_path / "large.npz"
    with path.open("wb") as stream:
        stream.seek(8 * 1024**2)
        stream.write(b"0")

    def forbidden(*args, **kwargs):
        raise AssertionError("oversize content must not be read or parsed")

    monkeypatch.setattr(type(path), "read_bytes", forbidden)
    monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(DataValidationError, match="oversized"):
        load_arrays(tmp_path, "large.npz", "a" * 64)


@pytest.mark.parametrize("mode", ["checksum", "name", "object", "shape", "expanded_size"])
def test_numeric_reader_rejects_untrusted_schema(tmp_path, monkeypatch, mode):
    arrays = {key: np.ones(5) for key in ("time_days", "flux", "flux_err", "period_days")}
    for pipeline in ("rm_0p5d", "rm_1p5d"):
        for key in (
            "epoch_days",
            "duration_days",
            "depth",
            "depth_err",
            "depth_snr",
            "power",
            "flux",
            "flux_err",
            "trend",
        ):
            arrays[f"{pipeline}_{key}"] = np.ones(5)
        for key in ("neighbor_counts", "segment_ids"):
            arrays[f"{pipeline}_{key}"] = np.ones(5, dtype=np.int64)
    if mode == "name":
        arrays["../escaped"] = np.ones(5)
    elif mode == "object":
        arrays["flux"] = np.array(["harmless fixture"] * 5, dtype=object)
    elif mode == "shape":
        arrays["flux"] = np.ones((1, 5))
    elif mode == "expanded_size":
        arrays["flux"] = np.zeros(60000)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    content = buffer.getvalue()
    (tmp_path / "case.npz").write_bytes(content)
    checksum = "a" * 64 if mode == "checksum" else acq.source_sha256(content)
    if mode in {"checksum", "name", "expanded_size"}:

        def forbidden(*args, **kwargs):
            raise AssertionError("invalid input must fail before NumPy parsing")

        monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(DataValidationError):
        load_arrays(tmp_path, "case.npz", checksum)


def test_numeric_reader_traversal(tmp_path):
    with pytest.raises(DataValidationError, match="unsafe"):
        load_arrays(tmp_path, "../escaped.npz", "a" * 64)
