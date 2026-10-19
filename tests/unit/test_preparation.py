"""Literal analytic references, bin boundaries, and preservation invariants."""

import json
from pathlib import Path

import numpy as np
import pytest

from transitstress.errors import DataValidationError
from transitstress.models import TimeMetadata
from transitstress.preparation import prepare_curve


def prepare(t, f=None, e=None):
    return prepare_curve(
        t,
        np.ones(len(t)) if f is None else f,
        np.ones(len(t)) if e is None else e,
        curve_id="fixture",
        source_sha256="a" * 64,
        time_metadata=TimeMetadata(None, "unknown", "unknown", "d", "linear", 1.0),
    )


def test_independent_t01_reference():
    reference = json.loads(Path("tests/fixtures/preparation_reference.json").read_text())
    raw, expected = reference["raw"], reference["bin"]
    curve = prepare(raw["time_days"], raw["flux"], raw["flux_err"])
    assert curve.time_days == pytest.approx([expected["time_days"]], abs=1e-16)
    assert curve.flux == pytest.approx([expected["normalized_flux"]])
    assert curve.flux_err == pytest.approx([expected["normalized_error"]])
    assert curve.support_counts.tolist() == [2]
    assert curve.time_metadata.normalization_factor == 12
    assert curve.original_end_days == 0.005


def test_exact_near_boundaries_day_boundary_and_gaps():
    # The first four nonzero offsets straddle the *declared q tolerance*, not a day tolerance.
    width = 10 / 1440
    t = np.array(
        [0, (1 - 2e-8) * width, (1 - 0.5e-8) * width, width, (1 + 2e-8) * width, 1, 1 + width, 2]
    )
    curve = prepare(t)
    assert curve.support_counts.tolist() == [2, 3, 1, 1, 1]
    assert len(curve.time_days) == 5  # Empty bins are absent, including the large gap.
    assert curve.original_end_days == 2
    assert curve.time_days[-3:].tolist() == pytest.approx([1, 1 + width, 2])


def test_sort_shift_no_mutation_and_stable_hash():
    t, f, e = np.array([102, 100, 101.0]), np.array([10, 12, 14.0]), np.array([1, 2, 3.0])
    originals = [a.copy() for a in (t, f, e)]
    curve = prepare(t, f, e)
    assert curve.time_days.tolist() == [0, 1, 2]
    assert curve.flux.tolist() == pytest.approx([1, 14 / 12, 10 / 12])
    assert curve.flux_err.tolist() == pytest.approx([2 / 12, 3 / 12, 1 / 12])
    assert curve.content_hash == prepare(t[::-1], f[::-1], e[::-1]).content_hash
    for original, current in zip(originals, (t, f, e), strict=True):
        np.testing.assert_array_equal(original, current)


@pytest.mark.parametrize(
    "t,f,e",
    [
        ([0, 0], [1, 1], [1, 1]),
        ([0, 1], [1, 0], [1, 1]),
        ([0, 1], [1, 1], [1e-300, 1]),
        ([0, 1], [1, 1], [1e300, 1]),
        ([0, float("nan")], [1, 1], [1, 1]),
        ([0, 1], [1e308, 1e308], [1, 1]),
    ],
)
def test_invalid_ranges_fail_without_clamping(t, f, e):
    with pytest.raises(DataValidationError):
        prepare(t, f, e)
