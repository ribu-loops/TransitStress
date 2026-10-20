"""T03 finite numerical/support boundaries and truth isolation; network blocked by conftest."""

import inspect
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from transitstress.config import load_config
from transitstress.eligibility import enumerate_cases, finalize_eligibility
from transitstress.errors import DataValidationError
from transitstress.injection import box_mask, inject_box, retention_ratio
from transitstress.models import InjectionSpec
from transitstress.support import window_layout, window_support


@pytest.mark.parametrize(
    "time,width,gap",
    [
        ([0.0, 0.0], 0.5, 0.5),
        ([1.0, 0.0], 0.5, 0.5),
        ([], 0.5, 0.5),
        ([0.0, float("nan")], 0.5, 0.5),
        ([0.0, float("inf")], 0.5, 0.5),
        ([0.0, 1.0], 0.0, 0.5),
        ([0.0, 1.0], -1.0, 0.5),
        ([0.0, 1.0], 0.5, 0.0),
        ([0.0, 1.0], float("inf"), 0.5),
        ([0.0, 1.0], 0.5, float("nan")),
        ([0.0, 1.0], True, 0.5),
        ([0.0, 1.0], 5e-324, 0.5),
        ([-1e308, 1e308], 0.5, 0.5),
        ([True, False], 0.5, 0.5),
        (["0", "1"], 0.5, 0.5),
    ],
)
def test_invalid_support_rejected(time, width, gap):
    with pytest.raises(DataValidationError):
        window_support(time, width, gap)


def test_blind_helper_rejects_truth_and_flux_keyword_inputs():
    assert set(inspect.signature(window_layout).parameters) == {
        "time_days",
        "window_days",
        "segment_gap_days",
    }
    for forbidden in ("truth", "flux", "injection_spec", "expected_period", "transit_mask"):
        with pytest.raises(TypeError):
            window_support([0.0, 1.0], 0.5, 0.5, **{forbidden: [True, False]})


def test_injection_and_ratio_overflow_are_errors_not_fake_measurements():
    with pytest.raises(DataValidationError, match="phase"):
        box_mask([1e308], InjectionSpec("overflow", 1.7e308, 1.6e308, 1.6e308, 0.1))
    # Reducing before subtraction handles this previously overflowing finite epoch.
    np.testing.assert_array_equal(
        box_mask([1e308], InjectionSpec("overflow-safe", 1.0, 0.1, -1e308, 0.1)), [True]
    )
    with pytest.raises(DataValidationError, match="underflow"):
        box_mask([0.0], InjectionSpec("underflow", 1.0, 5e-324, 0.0, 0.1))
    with pytest.raises(DataValidationError):
        retention_ratio(1e308, 1e-308)
    assert "injection" not in inspect.signature(window_support).parameters


def test_provisional_enumeration_and_frozen_finalization_stop_before_io(tmp_path):
    with pytest.raises(DataValidationError, match="provisional"):
        enumerate_cases({"status": "provisional"}, {}, None, "evaluation")
    config = load_config(Path("configs/protocol_v1.toml"))
    with pytest.raises(DataValidationError, match="frozen"):
        finalize_eligibility(
            replace(config, frozen=True),
            data_root=tmp_path / "absent",
            cache_root=tmp_path / "absent_cache",
        )
    assert not list(tmp_path.iterdir())


def test_injection_api_has_no_network_or_truth_in_support_dependency():
    # APIs accept explicit science inputs, no URL/shell/archive or all-purpose payload.
    assert set(inspect.signature(inject_box).parameters) == {"curve", "spec"}
    assert set(inspect.signature(finalize_eligibility).parameters) == {
        "config",
        "data_root",
        "cache_root",
    }
    np.testing.assert_array_equal(window_support([0.0], 0.5, 0.5), [1])


def test_final_flags_alone_do_not_allow_benchmark_use():
    from transitstress.acquisition import require_final_manifest

    for addition in ({}, {"t03_evidence_hash": "invalid"}):
        with pytest.raises(DataValidationError, match="T03"):
            require_final_manifest(
                {
                    "status": "final",
                    "support_validation": "complete",
                    "roles_provisional": False,
                    **addition,
                }
            )


def test_finalize_cli_failure_is_actionable_and_nonzero(tmp_path, monkeypatch, capsys):
    from transitstress.cli import main

    config = Path("configs/protocol_v1.toml").resolve()
    monkeypatch.chdir(tmp_path)
    assert main(["finalize", "--config", str(config)]) == 2
    assert "missing/corrupt" in capsys.readouterr().err
