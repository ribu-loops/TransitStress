"""Bounded T05 scoring/generation and finite paired-array regressions."""

from dataclasses import replace

import numpy as np
import pytest

from tests.unit.test_t05_evaluation import curve, original, spec
from transitstress.controls import gaussian_control, planned_result_rows
from transitstress.errors import DataValidationError
from transitstress.evaluation import coverage, paired_retention
from transitstress.models import ObservationMask


def test_extreme_event_count_rejected_before_event_storage():
    c = curve()
    with pytest.raises(DataValidationError, match="50000"):
        coverage(c, original(c), spec(period=1e-6, duration=1e-7))


def test_row_budget_rejected_before_iterating_supplied_cases():
    with pytest.raises(DataValidationError, match="oversized"):
        planned_result_rows((None,) * 501)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_retention_arrays_rejected(bad):
    c = curve()
    values = c.flux.copy()
    values[0] = bad
    with pytest.raises(DataValidationError, match="finite"):
        paired_retention(c, original(c), spec(), c.time_days, values, c.time_days, c.flux)


def test_retention_overflow_is_error_without_synthetic_zero_or_clipping():
    c = curve()
    with pytest.raises(DataValidationError, match="overflow"):
        paired_retention(
            c,
            original(c),
            spec(),
            c.time_days,
            np.full(len(c.flux), 1e308),
            c.time_days,
            np.full(len(c.flux), -1e308),
        )
    with pytest.raises(DataValidationError, match="align"):
        coverage(c, ObservationMask(np.ones(3, dtype=bool), "original", None), spec())


def test_gaussian_failure_is_preserved_without_resampling_or_clipping(monkeypatch):
    c = replace(curve(), flux_err=np.ones(len(curve().flux)))

    class FixedRng:
        def normal(self, *args):
            return np.full(len(c.flux), -2.0)

    monkeypatch.setattr(np.random, "Generator", lambda *args: FixedRng())
    with pytest.raises(DataValidationError, match="without resampling"):
        gaussian_control(c, 1, 0)
