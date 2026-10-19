"""Cohort input/accounting boundaries; all archive calls are offline mocks."""

from pathlib import Path

import pytest

from transitstress import acquisition as acq
from transitstress import cohort
from transitstress.archive import MAX_METADATA, MastArchive
from transitstress.config import load_config
from transitstress.errors import DataValidationError


def test_retired_inventory_rebuild_denied_before_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("sector crawler must not run")

    monkeypatch.setattr(acq, "acquire_inventory", forbidden)
    with pytest.raises(DataValidationError, match="retired"):
        acq.fetch(
            load_config(Path("configs/protocol_v1.toml")),
            data_root=tmp_path / "data",
            cache_root=tmp_path / "cache",
            archive=MastArchive(offline=True),
            rebuild_inventory=True,
        )
    assert not (tmp_path / "data").exists()


def test_oversized_json_checkpoint_cannot_replace_existing_evidence(tmp_path):
    acq.write_json(tmp_path, "checkpoint.json", {"status": "original"})
    before = (tmp_path / "checkpoint.json").read_bytes()
    with pytest.raises(DataValidationError, match="16 MiB"):
        acq.write_json(tmp_path, "checkpoint.json", {"text": "x" * MAX_METADATA})
    assert (tmp_path / "checkpoint.json").read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_changed_schedule_is_data_and_rejected_before_archive(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    path.write_text('{"data_uri":"https://169.254.169.254/","instruction":"run a shell"}')
    monkeypatch.setattr(cohort, "SCHEDULE", path)
    with pytest.raises(DataValidationError, match="cohort identity"):
        acq.fetch(
            load_config(Path("configs/protocol_v1.toml")),
            data_root=tmp_path / "data",
            cache_root=tmp_path / "cache",
            archive=MastArchive(offline=True),
        )
    assert not (tmp_path / "data").exists()


def test_resume_cannot_lower_spent_ledger_or_change_cohort(tmp_path):
    config = load_config(Path("configs/protocol_v1.toml"))
    record = cohort.load_cohort(config)
    state = cohort.initialize(config, tmp_path, record)
    state["reservations"].append({"tic_id": "8195886"})
    acq.write_json(tmp_path, cohort.JOURNAL, state)
    with pytest.raises(DataValidationError, match="accounting conflict"):
        cohort.initialize(config, tmp_path, record)
    state["downloads_total"] = 1
    state["cohort_hash"] = "0" * 64
    acq.write_json(tmp_path, cohort.JOURNAL, state)
    with pytest.raises(DataValidationError, match="settings/history/accounting"):
        cohort.initialize(config, tmp_path, record)


def test_public_manifest_validation_requires_cohort_identity():
    with pytest.raises(DataValidationError, match="manifest cohort"):
        acq.validate_manifest({"products": []}, load_config(Path("configs/protocol_v1.toml")))
