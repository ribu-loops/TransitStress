"""Runner counterexamples use controlled searches and separately known ephemerides."""

from pathlib import Path

import numpy as np
import pytest
from test_t02_cohort import setup_cohort as setup_cohort

from tests.unit.test_t04_science import curve
from tests.unit.test_t06_identity import repository as repository
from transitstress.artifacts import json_read
from transitstress.config import load_config
from transitstress.controls import gaussian_settings
from transitstress.errors import DataValidationError
from transitstress.experiment import CaseEngine, run_cases, writer_lock
from transitstress.masking import make_observation_mask
from transitstress.models import InjectionSpec, SearchResult
from transitstress.provenance import canonical_hash
from transitstress.reporting import analyze, validate_artifact
from transitstress.search import BLSOutput


@pytest.fixture
def fixture(monkeypatch):
    config = load_config(Path("configs/protocol_v1.toml"))
    c = curve()
    calls, generated = [], []

    def search(time, flux, error, grid, settings):
        # A controlled completed peak. Expected coverage/retention is calculated
        # from handwritten 2-day windows in the test, not from production injection.
        calls.append((time.copy(), flux.copy()))
        result = SearchResult(
            "ok", 2.0, 0.5, 0.125, 0.001, 0.0001, 10.0, 100.0, grid.checksum, 0.01
        )
        return BLSOutput(
            result, {"period_days": grid.periods_days, "power": np.ones(len(grid.periods_days))}
        )

    monkeypatch.setattr("transitstress.experiment.search_bls", search)
    from transitstress.controls import gaussian_control

    def gaussian(parent, master, replicate):
        generated.append((parent.content_hash, replicate))
        return gaussian_control(parent, master, replicate)

    monkeypatch.setattr("transitstress.experiment.gaussian_control", gaussian)
    cases = []
    for name in ("original", "random_missing"):
        mask = make_observation_mask(c, name, config.master_seed, config.masks)
        common = dict(
            curve_id=c.curve_id,
            prepared_hash=c.content_hash,
            mask_definition=name,
            mask_hash=mask.checksum,
        )
        for kind, spec, replicate in (
            ("uninjected_real", None, None),
            ("injection", InjectionSpec("independent", 2.0, 0.125, 0.5, 0.001), None),
            ("injection", InjectionSpec("shifted", 2.0, 0.125, 1.0, 0.001), None),
            ("gaussian_null", None, 0),
            ("gaussian_null", None, 1),
            ("gaussian_null", None, 2),
        ):
            item = dict(
                common, kind=kind, injection=spec, replicate=replicate, seed=None, sigma=None
            )
            if kind == "gaussian_null":
                item["seed"], item["sigma"] = gaussian_settings(c, config.master_seed, replicate)
            cases.append(dict(item, case_id=canonical_hash(item)))
    identity = {"split": "development", "source": "fixture-v1"}
    return config, c, tuple(cases), identity, calls, generated


def execute(root, fixture, **kw):
    config, c, cases, identity, _, _ = fixture
    return run_cases(
        root=root,
        identity=identity,
        cases=cases,
        engine=CaseEngine(config, {"input": c}, canonical_hash(identity)),
        provenance={"execution_commit": kw.pop("commit", "a" * 40)},
        **kw,
    )


def test_interruption_resume_skip_verified_controls_reuse_null_vectors_and_denominators(
    tmp_path, fixture
):
    config, c, cases, identity, calls, generated = fixture
    progress = []
    first = execute(
        tmp_path,
        fixture,
        cancel=lambda: len(progress) == 3,
        progress=lambda done, total: progress.append(done),
    )
    assert first["status"] == "incomplete" and first["actual_rows"] == 6
    assert len(calls) == 6
    with pytest.raises(DataValidationError, match="incomplete"):
        analyze(tmp_path)
    second = execute(tmp_path, fixture, resume=True, commit="b" * 40)
    assert second["status"] == "complete" and second["actual_rows"] == 24
    # Two background searches per mask total, not per injection; each null vector
    # generated once on the original basis, reused across both masks/configurations.
    assert len(calls) == 24
    assert len(generated) == len(set(generated)) == 3
    assert [p["execution_commit"] for p in second["provenance"]] == ["a" * 40, "b" * 40]
    run, rows = validate_artifact(tmp_path)
    assert len({r["case_id"] for r in rows}) == 12
    original = [r for r in rows if r["mask_name"] == "original" and r["case_kind"] == "injection"]
    exact = [r for r in original if r["true_epoch_days"] == 0.5]
    assert len(exact) == 2 and all(r["exact_recovery"] for r in exact)
    assert all(r["background_already_matches"] for r in exact)
    assert all(r["retention_ratio"]["value"] == pytest.approx(1.0) for r in exact)
    shifted = [r for r in original if r["true_epoch_days"] == 1.0]
    assert all(not r["exact_recovery"] for r in shifted)
    summary = analyze(tmp_path)
    all_counts = [r for r in summary["summaries"] if r["dimension"] == "all"]
    assert [r["planned"] for r in all_counts] == [4, 4]
    assert all(r["exact"] == 2 for r in all_counts)
    # A second clean execution agrees on numerical science and outcomes.
    repeat = tmp_path / "repeat"
    execute(repeat, fixture)
    other = json_read(repeat, "results.json")["rows"]
    ignored = {"detrend_seconds", "search_seconds", "elapsed_seconds"}
    for a, b in zip(rows, other, strict=True):
        assert {k: v for k, v in a.items() if k not in ignored} == {
            k: v for k, v in b.items() if k not in ignored
        }


def test_corrupt_case_preserved_recomputed_and_changed_identity_refused(tmp_path, fixture):
    execute(tmp_path, fixture)
    config, c, cases, identity, calls, _ = fixture
    cid = cases[0]["case_id"]
    path = tmp_path / "cases" / f"{cid}.json"
    path.write_text("corrupt")
    before = len(calls)
    run = execute(tmp_path, fixture, resume=True)
    assert len(calls) == before + 2
    assert run["invalidated"][0]["case_id"] == cid
    assert next((tmp_path / "cases").glob("*.invalid.json")).read_text() == "corrupt"
    changed = identity | {"source": "changed"}
    with pytest.raises(DataValidationError, match="resume identity"):
        run_cases(
            root=tmp_path,
            identity=changed,
            cases=cases,
            engine=CaseEngine(config, {"input": c}, canonical_hash(changed)),
            provenance={},
            resume=True,
        )


def test_search_detrend_and_input_failures_keep_all_rows(tmp_path, fixture, monkeypatch):
    def failed(*args, **kwargs):
        raise DataValidationError("flux: controlled failure; preserve evidence")

    monkeypatch.setattr("transitstress.experiment.detrend", failed)
    run = execute(tmp_path, fixture)
    assert run["actual_rows"] == 24
    _, rows = validate_artifact(tmp_path)
    assert all(r["status"] == "detrend_error" for r in rows)
    injections = [r for r in rows if r["case_kind"] == "injection"]
    assert all(r["coverage_eligible"] and r["exact_recovery"] is None for r in injections)
    assert all(r["scientific_outcome"] == "not_applicable" for r in rows)
    assert all(
        r["search"]["failure_reason"] == "flux: controlled failure; preserve evidence" for r in rows
    )
    summary = analyze(tmp_path)
    assert all(
        g["planned"] == g["eligible"] == g["technical_failures"] == 4
        for g in summary["summaries"]
        if g["dimension"] == "all"
    )


def test_cross_parent_background_binding_and_lock_rejected(tmp_path, fixture):
    config, c, cases, identity, _, _ = fixture
    engine = CaseEngine(config, {"input": c}, canonical_hash(identity))
    real = cases[0]
    engine.compute(real)
    key = next(iter(engine.backgrounds))
    engine.backgrounds[key].binding = "wrong-parent"
    with pytest.raises(DataValidationError, match="binding"):
        engine.compute(cases[1])
    with writer_lock(tmp_path), pytest.raises(DataValidationError, match="already active"):
        with writer_lock(tmp_path):
            pass


def test_truth_scoring_only_after_completed_searches(fixture, monkeypatch):
    config, c, cases, identity, calls, _ = fixture
    from transitstress.evaluation import evaluate_injection

    engine = CaseEngine(config, {"input": c}, canonical_hash(identity))
    engine.compute(cases[0])
    seen = []

    def scorer(curve, mask, spec, search, background, *args):
        seen.append(len(calls))
        assert search.status == background.status == "ok"
        return evaluate_injection(curve, mask, spec, search, background, *args)

    monkeypatch.setattr("transitstress.experiment.evaluate_injection", scorer)
    engine.compute(cases[1])
    assert seen == [3, 4]


def test_real_headless_csv_api_and_cli(tmp_path):
    from transitstress.cli import main
    from transitstress.workbench import single_case

    config = load_config(Path("configs/protocol_v1.toml"))
    csv = (
        "time_days,flux,flux_err,ignored\n"
        + "".join(f"{i / 100},1,0.001,=HARMLESS()\n" for i in range(3001))
    ).encode()
    output = tmp_path / "api"
    result = single_case(
        config,
        repository=Path.cwd(),
        output=output,
        csv_content=csv,
        confirm_days_and_linear_units=True,
        period_days=2.0,
        depth_ppm=1000.0,
        duration_hours=1.5,
        epoch_fraction=0.25,
    )
    assert result["actual_rows"] == 4
    run, rows = validate_artifact(output)
    assert run["identity"]["split"] == "exploratory"
    injection = [r for r in rows if r["case_kind"] == "injection"]
    assert all(r["exact_recovery"] for r in injection)
    assert all(r["retention_ratio"]["value"] == pytest.approx(1.0) for r in injection)
    assert all(r["units"]["absolute_epoch"] is None for r in rows)
    csv_path = tmp_path / "source.csv"
    csv_path.write_bytes(csv)
    assert (
        main(
            [
                "case",
                "--csv",
                str(csv_path),
                "--confirm-days-linear-units",
                "--period-days",
                "2",
                "--depth-ppm",
                "1000",
                "--duration-hours",
                "1.5",
                "--epoch-fraction",
                ".25",
                "--output",
                str(tmp_path / "cli"),
            ]
        )
        == 0
    )
    assert main(["validate-artifact", "--path", str(tmp_path / "cli")]) == 0
    assert main(["analyze", "--run", str(tmp_path / "cli")]) == 0
    for file in output.rglob("*"):
        if file.is_file():
            assert b"HARMLESS" not in file.read_bytes()
    import zipfile

    with zipfile.ZipFile(output / "evidence.zip") as archive:
        assert set(archive.namelist()) == {
            "results.csv",
            "results.json",
            "settings.json",
            "run.json",
            "summary.json",
            "report.html",
            "injected_arrays.npz",
            "background_arrays.npz",
        }
        assert archive.read("results.json") == (output / "results.json").read_bytes()
        assert archive.read("results.csv") == (output / "results.csv").read_bytes()


def test_actual_docs_commit_resume_and_science_commit_rejection(tmp_path, repository, fixture):
    import subprocess

    from transitstress.identity import execution_provenance, run_identity

    config, c, cases, _, calls, _ = fixture
    output = tmp_path / "run"

    def attempt(resume=False):
        identity = run_identity(
            repository,
            settings={"fixed": True},
            data={"parent": c.content_hash},
            split="development",
        )
        return run_cases(
            root=output,
            identity=identity,
            cases=cases,
            engine=CaseEngine(config, {"input": c}, canonical_hash(identity)),
            provenance=execution_provenance(repository),
            resume=resume,
        )

    first = attempt()
    before = len(calls)
    (repository / "README.md").write_text("actual documentation-only change\n")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=repository, check=True, capture_output=True)
    resumed = attempt(True)
    assert first["run_id"] == resumed["run_id"] and len(calls) == before
    assert len({p["execution_commit"] for p in resumed["provenance"]}) == 2
    (repository / "src/transitstress/science.py").write_text("VALUE = 9\n")
    with pytest.raises(DataValidationError, match="resume identity"):
        attempt(True)


@pytest.mark.parametrize("stage", ["search", "artifact", "gaussian", "retention"])
def test_stage_failures_are_retained_with_reason_and_original_denominators(
    tmp_path, fixture, monkeypatch, stage
):
    def failed(*args, **kwargs):
        raise DataValidationError(f"{stage}: controlled failure; preserve evidence")

    target = {
        "search": "search_bls",
        "artifact": "numeric_write",
        "gaussian": "gaussian_control",
        "retention": "paired_retention",
    }[stage]
    monkeypatch.setattr(f"transitstress.experiment.{target}", failed)
    run = execute(tmp_path, fixture)
    assert run["actual_rows"] == run["expected_rows"] == 24
    _, rows = validate_artifact(tmp_path)
    errors = [r for r in rows if r["status"] != "ok"]
    assert errors and all(
        r["reason"] and r["scientific_outcome"] == "not_applicable" for r in errors
    )
    if stage == "retention":
        assert len(errors) == 8 and all(r["exact_recovery"] is None for r in errors)
    if stage == "gaussian":
        assert len(errors) == 12
        assert all(r["case_kind"] == "gaussian_null" for r in errors)


def test_keyboard_interrupt_between_cases_keeps_committed_pair_and_resumes(tmp_path, fixture):
    def interrupt(done, total):
        if done == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path, fixture, progress=interrupt)
    run = json_read(tmp_path, "run.json")
    assert run["status"] == "incomplete" and run["actual_rows"] == 2
    assert execute(tmp_path, fixture, resume=True)["actual_rows"] == 24


def test_csv_in_memory_and_synthetic_built_in_do_not_persist_upload(tmp_path, fixture, monkeypatch):
    from transitstress.workbench import single_case

    config, c, *_ = fixture
    monkeypatch.setattr("transitstress.workbench.load_split", lambda *a: ({}, {"8195886": c}))

    def forbidden(*args, **kwargs):
        raise AssertionError("in-memory API attempted persistence")

    monkeypatch.setattr("transitstress.workbench.numeric_write", forbidden)
    for input_args in (
        dict(built_in="8195886"),
        dict(
            csv_content=(
                "time_days,flux,flux_err\n" + "".join(f"{i / 100},1,.001\n" for i in range(3001))
            ).encode(),
            confirm_days_and_linear_units=True,
        ),
    ):
        result = single_case(
            config,
            repository=Path.cwd(),
            period_days=2.0,
            depth_ppm=1000.0,
            duration_hours=3.0,
            epoch_fraction=0.25,
            **input_args,
        )
        assert result["actual_rows"] == 4 and len(result["details"]) == 2


def test_evaluation_execution_refused_before_any_source_loading(tmp_path, monkeypatch):
    from dataclasses import replace

    from transitstress.experiment import experiment

    def forbidden(*args, **kwargs):
        raise AssertionError("evaluation execution entered without freeze")

    monkeypatch.setattr("transitstress.experiment.load_split", forbidden)
    config = replace(load_config(Path("configs/protocol_v1.toml")), frozen=False)
    with pytest.raises(DataValidationError, match="G2"):
        experiment(
            config,
            repository=Path.cwd(),
            config_path=Path("configs/protocol_v1.toml"),
            manifest_path=Path("data/manifest.json"),
            cache=Path(".cache/transitstress"),
            output=tmp_path,
            split="evaluation",
        )


def test_html_metadata_stays_inert_and_environment_canary_absent(tmp_path, fixture, monkeypatch):
    monkeypatch.setenv("TRANSITSTRESS_TEST_CANARY", "harmless-private-canary-t06")
    execute(tmp_path, fixture)
    import transitstress.reporting as reporting

    original = reporting.validate_artifact
    payload = '</script><img src="https://example.invalid/canary">'

    def malicious_label(root):
        run, rows = original(root)
        for row in rows:
            row["curve_id"] = payload
        return run, rows

    monkeypatch.setattr(reporting, "validate_artifact", malicious_label)
    reporting.analyze(tmp_path)
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert payload not in report
    assert "&lt;/script&gt;&lt;img" in report
    from html.parser import HTMLParser

    class Resources(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert not (tag in ("img", "script") and "src" in dict(attrs))

    Resources().feed(report)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"harmless-private-canary-t06" not in path.read_bytes()


def test_final_cohort_enumeration_survives_future_gate_flag_without_rewriting_pins(
    tmp_path, setup_cohort
):
    from dataclasses import replace

    from test_t02_cohort import run

    from transitstress.controls import enumerate_experiment
    from transitstress.eligibility import finalize_eligibility
    from transitstress.experiment import load_split

    run(tmp_path, setup_cohort)
    config = setup_cohort[0]
    data, cache = tmp_path / "data", tmp_path / "cache"
    finalize_eligibility(config, data_root=data, cache_root=cache)
    original = (data / "manifest.json").read_bytes()
    frozen = replace(config, frozen=True)
    manifest, curves = load_split(frozen, data / "manifest.json", cache, "development")
    cases = enumerate_experiment(manifest, curves, frozen, "development")
    assert len(cases) == 78 and (data / "manifest.json").read_bytes() == original


def test_artifact_validator_checks_csv_values_even_with_updated_transport_checksum(
    tmp_path, fixture
):
    from transitstress import acquisition as acq
    from transitstress.artifacts import json_write

    execute(tmp_path, fixture)
    csv = tmp_path / "results.csv"
    # A valid schema and matching transport checksum do not prove science values.
    text = csv.read_text(encoding="utf-8")
    lines = text.splitlines()
    fields = lines[0].split(",")
    record = lines[1].split(",")
    record[fields.index("period_days")] = "9.0"
    lines[1] = ",".join(record)
    content = ("\n".join(lines) + "\n").encode()
    csv.write_bytes(content)
    run = json_read(tmp_path, "run.json")
    run["file_checksums"]["results.csv"] = acq.source_sha256(content)
    json_write(tmp_path, "run.json", run)
    with pytest.raises(DataValidationError, match="CSV projection"):
        validate_artifact(tmp_path)
