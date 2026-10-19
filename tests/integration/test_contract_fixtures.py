"""Analytic references carried through T01 contracts; no preparation/search execution.

Reference arithmetic here is deliberately direct. T02/T03/T05 must compare their
future production operations to these saved literals, rather than regenerate them.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from transitstress.config import load_config
from transitstress.models import InjectionSpec, PreparedCurve, TimeMetadata
from transitstress.provenance import canonical_hash

FIXTURES = Path("tests/fixtures")


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_hand_weighted_bin_normalization_and_ppm_contracts():
    reference = fixture("preparation_reference.json")
    # Median of 10 and 14 is 12. In raw units w=(1,1/4), sum(w)=5/4.
    # Weighted flux=(10+14/4)/(5/4)=10.8; time=(0+.005/4)/(5/4)=.001.
    assert (10 + 14 / 4) / (5 / 4) == pytest.approx(reference["bin"]["raw_flux"])
    assert (0.005 / 4) / (5 / 4) == pytest.approx(reference["bin"]["time_days"])
    assert (4 / 5) ** 0.5 == pytest.approx(reference["bin"]["raw_error"])
    assert np.array(reference["raw"]["flux"]) / 12 == pytest.approx(reference["normalized_flux"])
    assert np.array(reference["raw"]["flux_err"]) / 12 == pytest.approx(
        reference["normalized_error"]
    )
    assert reference["bin"]["raw_error"] / 12 == pytest.approx(reference["bin"]["normalized_error"])
    metadata = TimeMetadata(None, "unknown", "unknown", "days", "fixture_linear_flux", 12.0)
    prepared = PreparedCurve(
        "analytic-bin",
        np.array([reference["bin"]["time_days"]]),
        np.array([reference["bin"]["normalized_flux"]]),
        np.array([reference["bin"]["normalized_error"]]),
        0.0,
        0.005,
        0.0,
        np.array([2]),
        metadata,
        "a" * 64,
    )
    assert prepared.flux[0] == 0.9
    spec = InjectionSpec("analytic", 2.0, 0.5, 0.5, reference["ppm"]["relative_depth"])
    assert spec.depth_ppm == reference["ppm"]["depth_ppm"]
    assert spec.depth * 100 == pytest.approx(reference["ppm"]["percent"])
    assert len(prepared.content_hash) == 64
    assert len(canonical_hash(load_config(Path("configs/protocol_v1.toml")))) == 64


def test_independent_phase_boundary_reference_is_self_consistent():
    reference = fixture("injection_reference.json")
    # Evaluate the written equation directly, without production injection code.
    times = np.array(reference["time_days"])
    phase = ((times - 0.5 + 1) % 2) - 1
    np.testing.assert_array_equal(phase, reference["centered_phase_days"])
    np.testing.assert_array_equal((phase >= -0.25) & (phase < 0.25), reference["in_transit"])
    np.testing.assert_array_equal(
        1 - 0.00075 * np.array(reference["in_transit"]), reference["expected_flux_from_unity"]
    )
    shifted = ((times - 4.5 + 1) % 2) - 1
    np.testing.assert_array_equal(shifted, phase)


def test_separately_written_evaluation_reference():
    reference = fixture("evaluation_reference.json")
    assert reference["full_centers_days"] == [0.5, 2.5, 4.5]
    assert reference["good_fit"]["epoch_days"] - reference["true_epoch_days"] == 2.0
    # The right period with centers shifted by .5 d exceeds D/2=.25 d.
    assert reference["wrong_phase_fit"]["relative_period_error"] == 0
    assert reference["wrong_phase_fit"]["maximum_timing_error_days"] > 0.25
    assert reference["wrong_phase_fit"]["exact"] is False
