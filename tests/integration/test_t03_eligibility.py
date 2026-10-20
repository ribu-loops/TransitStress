"""Offline promotion/repeat/interruption and ordered exclusion/reserve boundaries."""

import copy
from dataclasses import replace

import pytest
from test_t02_cohort import run
from test_t02_cohort import setup_cohort as setup_cohort

from transitstress import acquisition as acq
from transitstress import eligibility
from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash


def finalize(tmp_path, setup):
    return eligibility.finalize_eligibility(
        setup[0], data_root=tmp_path / "data", cache_root=tmp_path / "cache"
    )


def test_full_offline_promotion_preserves_pins_history_accounting_and_repeat(
    tmp_path, setup_cohort
):
    run(tmp_path, setup_cohort)
    data = tmp_path / "data"
    original = acq.read_json(data, "manifest.json")
    journal_before = (data / "cohort_acquisition.json").read_bytes()
    result = finalize(tmp_path, setup_cohort)
    final = acq.read_json(data, "manifest.json")
    evidence = acq.read_json(data, eligibility.RECORD)
    assert result["injection_case_counts"] == {"development": 54, "evaluation": 135}
    assert result["new_downloads"] == 0 and result["downloads_total"] == 8  # Synthetic; no history.
    assert evidence["input_manifest"] == original
    assert evidence["input_manifest_hash"] == canonical_hash(original)
    assert evidence["unattempted_reserves"] == ["24265684", "24265755"]
    assert final["products"] == original["products"]
    assert not final["roles_provisional"] and final["support_validation"] == "complete"
    assert canonical_hash(evidence) == final["t03_evidence_hash"]
    assert final["t03_settings_hash"] == canonical_hash(setup_cohort[0])
    with pytest.raises(DataValidationError, match="case settings"):
        eligibility.enumerate_cases(
            final, {}, replace(setup_cohort[0], master_seed=123), "development"
        )
    assert (data / "cohort_acquisition.json").read_bytes() == journal_before
    before = {p.name: p.read_bytes() for p in data.glob("*.json")}
    assert finalize(tmp_path, setup_cohort) == result
    # Existing fetch still reuses exact final pins, with no cohort reselection.
    run(tmp_path, setup_cohort, setup_cohort[2](offline=True))
    assert {p.name: p.read_bytes() for p in data.glob("*.json")} == before


def test_interruption_after_evidence_resumes_identical_promotion(
    tmp_path, setup_cohort, monkeypatch
):
    run(tmp_path, setup_cohort)
    write = acq.write_json

    def interrupted(root, name, value):
        if name == "manifest.json" and value.get("status") == "final":
            raise KeyboardInterrupt("controlled promotion interruption")
        write(root, name, value)

    with monkeypatch.context() as patch:
        patch.setattr(acq, "write_json", interrupted)
        with pytest.raises(KeyboardInterrupt):
            finalize(tmp_path, setup_cohort)
    data = tmp_path / "data"
    before = (data / eligibility.RECORD).read_bytes()
    assert acq.read_json(data, "manifest.json")["status"] == "provisional"
    finalize(tmp_path, setup_cohort)
    assert (data / eligibility.RECORD).read_bytes() == before


def test_rejected_candidate_requires_next_reserve_no_download_or_promotion(
    tmp_path, setup_cohort, monkeypatch
):
    run(tmp_path, setup_cohort)
    real = eligibility.support_evidence
    visited = []

    def reject_one(curve, config):
        audit = real(curve, config)
        visited.append(curve.curve_id)
        if "tic8196058-" in curve.curve_id:
            audit.update(passed=False, reasons=["controlled insufficient time support"])
        return audit

    monkeypatch.setattr(eligibility, "support_evidence", reject_one)
    data = tmp_path / "data"
    before = {p.name: p.read_bytes() for p in data.glob("*.json")}
    with pytest.raises(DataValidationError, match="reserve 24265684.*bounded"):
        finalize(tmp_path, setup_cohort)
    assert len(visited) == 8
    blocked = acq.read_json(data, "t03_blocked.json")
    assert blocked["next_tic"] == "24265684"
    assert any(a["status"] == "support_excluded" for a in blocked["audits"])
    for name, content in before.items():
        assert (data / name).read_bytes() == content
    assert not (data / eligibility.RECORD).exists()


@pytest.mark.parametrize("problem", ["missing", "corrupt", "evidence", "final", "ledger"])
def test_conflicts_stop_without_reselection(tmp_path, setup_cohort, problem):
    run(tmp_path, setup_cohort)
    data, cache = tmp_path / "data", tmp_path / "cache"
    if problem in ("evidence", "final"):
        finalize(tmp_path, setup_cohort)
        name = eligibility.RECORD if problem == "evidence" else "manifest.json"
        changed = copy.deepcopy(acq.read_json(data, name))
        changed["verified_utc" if problem == "evidence" else "finalized_utc"] = "changed"
        acq.write_json(data, name, changed)
    elif problem == "ledger":
        acq.write_json(cache, "pinned_downloads.json", {"downloads": 1})
    else:
        manifest = acq.read_json(data, "manifest.json")
        source = cache / acq.cache_name(manifest["products"][0])
        if problem == "missing":
            source.unlink()
        else:
            source.write_bytes(b"invalid fixture")
    before = (data / "manifest.json").read_bytes()
    with pytest.raises(DataValidationError):
        finalize(tmp_path, setup_cohort)
    assert (data / "manifest.json").read_bytes() == before
