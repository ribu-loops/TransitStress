"""T05 primitives integrated with blind detrending and synthetic final cohorts."""

import json
from collections import Counter

import numpy as np
import pytest
from test_t02_cohort import run
from test_t02_cohort import setup_cohort as setup_cohort

from tests.unit.test_t04_science import curve
from transitstress import acquisition as acq
from transitstress.controls import enumerate_experiment, planned_result_rows
from transitstress.detrending import detrend
from transitstress.eligibility import enumerate_cases, finalize_eligibility
from transitstress.evaluation import paired_retention
from transitstress.models import DetrendConfig, InjectionSpec, ObservationMask
from transitstress.provenance import canonical_json


def test_evaluator_has_no_execution_capability_and_cannot_mutate_completed_searches(monkeypatch):
    from tests.unit.test_t05_evaluation import evaluate, peak, spec

    def forbidden(*args, **kwargs):
        raise AssertionError("truth scoring attempted blind processing")

    monkeypatch.setattr("transitstress.search.search_bls", forbidden)
    monkeypatch.setattr("transitstress.search.build_search_grid", forbidden)
    monkeypatch.setattr("transitstress.detrending.detrend", forbidden)
    completed = peak()
    before = canonical_json(completed)
    a = evaluate(search=completed, background=completed)
    b = evaluate(truth=spec(epoch=1.0), search=completed, background=completed)
    assert a.exact_recovery and b.exact_recovery is not True
    assert canonical_json(completed) == before


@pytest.mark.parametrize("pipeline,width", [("rm_0p5d", 0.5), ("rm_1p5d", 1.5)])
def test_constant_background_nonabsorbing_trend_retains_box(pipeline, width):
    # .0625-day dips occupy less than half either median window. This literal
    # interval fixture is separate from production injection and truth masking.
    c = curve()
    injected = c.flux.copy()
    for center in (
        0.5,
        2.5,
        4.5,
        6.5,
        8.5,
        10.5,
        12.5,
        14.5,
        16.5,
        18.5,
        20.5,
        22.5,
        24.5,
        26.5,
        28.5,
    ):
        injected[(c.time_days >= center - 0.03125) & (c.time_days < center + 0.03125)] -= 0.001
    before, injected_before = c.content_hash, injected.copy()
    config = DetrendConfig(pipeline, width)
    background = detrend(c.time_days, c.flux, c.flux_err, config)
    processed = detrend(c.time_days, injected, c.flux_err, config)
    np.testing.assert_array_equal(background.trend, np.ones(len(injected)))
    np.testing.assert_array_equal(processed.trend, np.ones(len(injected)))
    # Truth first enters here, after both independent detrending calls finish.
    result = paired_retention(
        c,
        ObservationMask(np.ones(len(injected), dtype=bool), "original", None),
        InjectionSpec("unit-only", 2.0, 0.0625, 0.5, 0.001),
        c.time_days,
        background.flux,
        c.time_days,
        processed.flux,
    )
    assert result.retained_depth.value == pytest.approx(0.001)
    assert result.ratio.value == pytest.approx(1.0)
    assert c.content_hash == before
    np.testing.assert_array_equal(injected, injected_before)


def test_final_synthetic_cohort_counts_ids_control_semantics_and_json(
    tmp_path, setup_cohort, monkeypatch
):
    run(tmp_path, setup_cohort)
    config = setup_cohort[0]
    data, cache = tmp_path / "data", tmp_path / "cache"
    finalize_eligibility(config, data_root=data, cache_root=cache)
    manifest = acq.read_json(data, "manifest.json")
    curves = {
        p["tic_id"]: acq.verify_pinned(p, acq.cached_source(cache, p), config, cache)
        for p in manifest["products"]
    }

    # Enumerating a plan must not generate vectors, detrend, or search.
    def forbidden(*args, **kwargs):
        raise AssertionError("enumeration executed science")

    monkeypatch.setattr("transitstress.controls.gaussian_control", forbidden)
    monkeypatch.setattr("transitstress.search.search_bls", forbidden)
    before = canonical_json(manifest)
    all_ids = []
    for split, expected in [("development", (54, 6, 18)), ("evaluation", (135, 15, 45))]:
        cases = enumerate_experiment(manifest, curves, config, split)
        assert Counter(c["kind"] for c in cases) == dict(
            zip(("injection", "uninjected_real", "gaussian_null"), expected, strict=True)
        )
        old = enumerate_cases(manifest, curves, config, split)
        assert [c["case_id"] for c in cases if c["kind"] == "injection"] == [
            c["case_id"] for c in old
        ]
        rows = planned_result_rows(cases)
        saved = json.loads(canonical_json(rows))
        assert len(saved) == (156 if split == "development" else 390)
        identities = [(r["case_id"], r["pipeline_id"]) for r in saved]
        assert len(identities) == len(set(identities))
        assert set(Counter(r["case_id"] for r in saved).values()) == {2}
        all_ids.extend(c["case_id"] for c in cases)
        for c in cases:
            if c["kind"] != "injection":
                assert c["injection"] is None
                assert (
                    c["seed"] is None if c["kind"] == "uninjected_real" else type(c["seed"]) is int
                )
        nulls = [c for c in cases if c["kind"] == "gaussian_null"]
        for c in nulls:
            siblings = [
                n
                for n in nulls
                if n["curve_id"] == c["curve_id"] and n["replicate"] == c["replicate"]
            ]
            assert len(siblings) == 3 and len({n["seed"] for n in siblings}) == 1
    assert len(all_ids) == len(set(all_ids)) == 273
    assert canonical_json(manifest) == before
