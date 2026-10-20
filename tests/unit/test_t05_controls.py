"""Independent seed encoding and original-basis Gaussian references."""

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest

from tests.unit.test_t05_evaluation import curve
from transitstress.controls import gaussian_control, gaussian_settings, planned_result_rows
from transitstress.errors import DataValidationError
from transitstress.masking import make_observation_mask


def test_gaussian_seed_sigma_noise_and_shared_mask_basis():
    c = curve()
    errors = np.array([0.001] * 10 + [0.003] + [0.005] * 10)
    c = replace(c, flux_err=errors)
    before = c.content_hash
    seeds = []
    for replicate in range(3):
        # Direct standard-library encoding, independent of production hash helpers.
        encoded = json.dumps(
            dict(
                master_seed=20261019,
                curve_id=c.curve_id,
                purpose="gaussian_null",
                replicate=replicate,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        seed = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "little")
        expected = 1 + 0.003 * np.random.Generator(np.random.PCG64(seed)).standard_normal(
            len(errors)
        )
        generated = gaussian_control(c, 20261019, replicate)
        assert generated.seed == seed and generated.sigma == 0.003
        assert generated.parent_prepared_hash == before
        assert generated.curve.flux == pytest.approx(expected, rel=0, abs=1e-15)
        np.testing.assert_array_equal(generated.curve.flux_err, np.full(len(errors), 0.003))
        np.testing.assert_array_equal(generated.curve.time_days, c.time_days)
        assert generated.curve.time_metadata == c.time_metadata
        assert generated.curve.original_start_days == c.original_start_days
        assert generated.curve.original_end_days == c.original_end_days
        for name in ("original", "random_missing", "central_block"):
            mask = make_observation_mask(c, name, 20261019)
            same = make_observation_mask(generated.curve, name, 20261019)
            assert same.checksum == mask.checksum
            np.testing.assert_allclose(generated.curve.flux[mask.keep], expected[mask.keep])
        seeds.append(seed)
    assert len(set(seeds)) == 3 and c.content_hash == before


def test_even_median_sigma_and_seed_validation():
    c = replace(curve([0, 1, 2, 3]), flux_err=np.array([0.001, 0.003, 0.005, 0.007]))
    assert gaussian_settings(c, 1, 0)[1] == 0.004
    for replicate in (-1, 3, True, 0.5):
        with pytest.raises(DataValidationError, match="replicate"):
            gaussian_settings(c, 1, replicate)


def test_planned_rows_preserve_case_id_and_pipeline_identity_without_execution():
    cases = (dict(case_id="a" * 64, kind="uninjected_real", injection=None),)
    rows = planned_result_rows(cases)
    assert [(r["case_id"], r["pipeline_id"]) for r in rows] == [
        ("a" * 64, "rm_0p5d"),
        ("a" * 64, "rm_1p5d"),
    ]
    assert all(r["injection"] is None for r in rows)
    with pytest.raises(DataValidationError, match="duplicated"):
        planned_result_rows(cases * 2)
