"""Synthetic positives/negatives constructed independently of injection code."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from transitstress import acquisition as acq
from transitstress.config import load_config
from transitstress.models import PreparedCurve, TimeMetadata
from transitstress.sanity import blind_pair, run_sanity


def synthetic(period=None):
    ref = json.loads(Path("tests/fixtures/bls_reference.json").read_text())
    t = np.arange(0, ref["baseline_days"], ref["cadence_days"])
    flux = np.ones(len(t))
    if period is not None:
        # Explicit independently enumerated transit intervals, not modulo/inject_box.
        for event in range(-1, 20):
            center = ref["epoch_days"] + event * period
            flux[(t >= center - 0.0625) & (t < center + 0.0625)] -= ref["depth"]
    c = PreparedCurve(
        "synthetic-fixture-only",
        t,
        flux,
        np.full(len(t), ref["error"]),
        0.0,
        ref["baseline_days"],
        0.0,
        np.ones(len(t), dtype=int),
        TimeMetadata(None, "unknown", "unknown", "days", "synthetic", 1.0),
        "b" * 64,
    )
    return c, ref


@pytest.mark.parametrize("period", [2.9137, 4.28791])
def test_broad_off_grid_synthetic_positive_both_windows(period):
    c, ref = synthetic(period)
    before = c.content_hash
    grid, output, arrays = blind_pair(c, load_config(Path("configs/protocol_v1.toml")))
    assert period not in arrays["period_days"]
    assert grid["first_period_days"] == 0.75 and grid["last_period_days"] > 9.99
    for r in output["searches"].values():
        assert r["status"] == "ok"
        assert abs(r["period_days"] - period) / period < ref["relative_period_tolerance"]
        assert r["depth"] > 0.008
        assert r["grid_hash"] == grid["hash"]
    assert c.content_hash == before


def test_negative_unity_no_depth_no_significance_and_hidden_truth_isolation():
    c, _ = synthetic()
    config = load_config(Path("configs/protocol_v1.toml"))
    grid, output, arrays = blind_pair(c, config)
    for r in output["searches"].values():
        assert abs(r["depth"]) < 1e-14 and abs(r["power"]) < 1e-14
    # Hidden literature/injection metadata changes outside the typed blind inputs.
    hidden_truth = {"period": 6.27, "transit_mask": np.zeros(len(c.time_days), dtype=bool)}
    hidden_truth.update(period=0.9, transit_mask=np.ones(len(c.time_days), dtype=bool))
    grid2, output2, arrays2 = blind_pair(replace(c), config)
    assert grid == grid2
    for name in arrays:
        np.testing.assert_array_equal(arrays[name], arrays2[name])
    for name in output["searches"]:
        a, b = output["searches"][name], output2["searches"][name]
        assert {k: v for k, v in a.items() if k != "elapsed_seconds"} == {
            k: v for k, v in b.items() if k != "elapsed_seconds"
        }
    assert "truth" not in asdict(config.search)


def test_command_reference_is_read_only_after_both_searches_and_cannot_guide_them(
    tmp_path, monkeypatch
):
    """Mock source retrieval, retain real command orchestration and BLS calls.

    This fixture is synthetic and never published as the real known-signal run.
    """
    c, _ = synthetic(2.9137)
    c = replace(c, time_metadata=TimeMetadata(2458324.0, "btjd", "tdb", "d", "fixture", 1.0))
    config = load_config(Path("configs/protocol_v1.toml"))
    for name in ("manifest.json", "t03_eligibility.json", "sanity_reference.json"):
        (tmp_path / name).write_bytes((Path("data") / name).read_bytes())
    evidence = acq.read_json(tmp_path, "t03_eligibility.json")
    saved = next(a for a in evidence["audits"] if a["tic_id"] == "261136679")
    monkeypatch.setattr(acq, "cached_source", lambda *a: b"synthetic source fixture")
    monkeypatch.setattr(acq, "verify_pinned", lambda *a: c)
    monkeypatch.setattr("transitstress.sanity.support_evidence", lambda *a: saved)
    events = []
    read = acq.read_json

    def traced_read(root, name):
        if name == "sanity_reference.json":
            assert events[-1] == "both searches complete"
        return read(root, name)

    def traced_pair(*args):
        result = blind_pair(*args)
        events.append("both searches complete")
        return result

    monkeypatch.setattr(acq, "read_json", traced_read)
    monkeypatch.setattr("transitstress.sanity.blind_pair", traced_pair)
    first = run_sanity(
        config,
        manifest_path=tmp_path / "manifest.json",
        cache_root=tmp_path,
        output_root=tmp_path / "first",
    )
    reference = read(tmp_path, "sanity_reference.json")
    reference.update(period_days=0.91, epoch_bjd_tdb=2458311.0)
    acq.write_json(tmp_path, "sanity_reference.json", reference)
    second = run_sanity(
        config,
        manifest_path=tmp_path / "manifest.json",
        cache_root=tmp_path,
        output_root=tmp_path / "second",
    )
    assert first["grid"] == second["grid"]
    assert first["numerical_file_sha256"] == second["numerical_file_sha256"]
    assert first["post_search_comparison"] != second["post_search_comparison"]
    for name in first["searches"]:
        assert {k: v for k, v in first["searches"][name].items() if k != "elapsed_seconds"} == {
            k: v for k, v in second["searches"][name].items() if k != "elapsed_seconds"
        }
    with np.load(tmp_path / "first/sanity_arrays.npz", allow_pickle=False) as arrays:
        assert len(arrays["period_days"]) == first["grid"]["length"]
        assert all(arrays[name].dtype.kind in "fi" for name in arrays.files)
