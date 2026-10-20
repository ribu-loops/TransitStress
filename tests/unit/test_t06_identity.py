"""Semantic identity includes added helpers and excludes Git/docs provenance."""

import json
import subprocess
from pathlib import Path

import pytest

from transitstress.identity import execution_provenance, run_identity
from transitstress.provenance import canonical_hash


@pytest.fixture
def repository(tmp_path):
    (tmp_path / "src/transitstress").mkdir(parents=True)
    for name, content in {
        "src/transitstress/science.py": "VALUE = 1\n",
        "pyproject.toml": "name = 'fixture'\n",
        ".python-version": "3.12\n",
        "uv.lock": "fixture-lock\n",
        "README.md": "first\n",
    }.items():
        (tmp_path / name).write_text(content)
    commands = (
        ["git", "init"],
        ["git", "config", "user.email", "fixture@example.invalid"],
        ["git", "config", "user.name", "Test fixture"],
        ["git", "add", "."],
        ["git", "commit", "-m", "fixture"],
    )
    for command in commands:
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def identity(root, **changes):
    return run_identity(
        root,
        **(dict(settings={"duration": 0.125}, data={"source": "a"}, split="development") | changes),
    )


def test_actual_docs_only_commit_keeps_identity_and_records_both_heads(repository):
    first, provenance = identity(repository), execution_provenance(repository)
    (repository / "README.md").write_text("second\n")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "docs only"], cwd=repository, check=True, capture_output=True
    )
    second = execution_provenance(repository)
    assert second["execution_commit"] != provenance["execution_commit"]
    assert first == identity(repository)
    assert not second["dirty"]
    assert "recorded_utc" not in first
    assert json.loads(json.dumps(first)) == first


@pytest.mark.parametrize("change", ["edit", "add", "delete", "lock", "package", "data", "settings"])
def test_scientific_dependency_data_settings_changes_invalidate(repository, change):
    original = canonical_hash(identity(repository))
    overrides = {}
    if change == "edit":
        (repository / "src/transitstress/science.py").write_text("VALUE = 2\n")
    elif change == "add":
        (repository / "src/transitstress/new_helper.py").write_text("VALUE = 1\n")
    elif change == "delete":
        (repository / "src/transitstress/science.py").unlink()
    elif change == "lock":
        (repository / "uv.lock").write_text("changed-lock\n")
    elif change == "package":
        (repository / "pyproject.toml").write_text("changed-package\n")
    else:
        overrides[change] = {"changed": True}
    assert canonical_hash(identity(repository, **overrides)) != original


def test_paths_and_transport_timestamps_do_not_enter_identity(repository):
    a = identity(repository)
    assert all(not Path(k).is_absolute() for k in a["scientific_files"])
    assert "execution_commit" not in a


def test_gate_flag_keeps_original_eligibility_pins_but_science_changes_do_not():
    from dataclasses import replace

    from transitstress.config import eligibility_reference_hash, load_config

    config = load_config(Path("configs/protocol_v1.toml"))
    assert eligibility_reference_hash(config) == canonical_hash(config)
    assert eligibility_reference_hash(replace(config, frozen=True)) == canonical_hash(config)
    assert eligibility_reference_hash(replace(config, master_seed=1)) != canonical_hash(config)
