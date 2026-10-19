import io
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from transitstress.config import load_config
from transitstress.errors import DataValidationError
from transitstress.tess_input import basic_qc_reasons, prepare_tess


def test_qc_counts_explicit_units_epoch_and_support(tess_factory):
    config = load_config(Path("configs/protocol_v1.toml"))
    t = 1325 + np.arange(18001) / 720
    f, e, q = np.full(len(t), 1000.0), np.full(len(t), 2.0), np.zeros(len(t), dtype=int)
    q[0] = 1
    f[0] = np.nan  # Overlapping reasons must not be reported as an exclusive sum.
    f[1] = 0
    e[2] = -1
    content = tess_factory(time=t, flux=f, error=e, quality=q)
    curve, audit = prepare_tess(content, tic_id="1", config=config)
    assert audit["input_rows"] == 18001
    assert audit["quality_selected_rows"] == 18000
    assert audit["retained_rows"] == 17998
    assert audit["removed_union_rows"] == 3
    assert audit["rejection_counts"]["nonfinite_flux"] == 1
    assert sum(audit["rejection_counts"].values()) == 4
    assert curve.time_metadata.absolute_epoch == pytest.approx(2458325 + 3 / 720, abs=5e-10)
    assert curve.time_metadata.time_scale == "tdb"
    assert audit["source_baseline_days"] == pytest.approx(25 - 3 / 720)
    assert audit["support_rows"] == 17998
    assert curve.flux == pytest.approx(np.ones(len(curve.flux)))
    assert curve.flux_err[0] == pytest.approx(0.002 / np.sqrt(5))
    assert not basic_qc_reasons(curve, audit, config)


@pytest.mark.parametrize(
    "headers", [{"TIMESYS": "UTC"}, {"TIMEUNIT": "s"}, {"TIMEDEL": 1800 / 86400}, {"BJDREFI": 0}]
)
def test_metadata_mismatch_rejected(tess_factory, headers):
    with pytest.raises(DataValidationError):
        prepare_tess(
            tess_factory(headers=headers),
            tic_id="1",
            config=load_config(Path("configs/protocol_v1.toml")),
        )


def test_duplicate_and_finite_filter(tess_factory):
    config = load_config(Path("configs/protocol_v1.toml"))
    with pytest.raises(DataValidationError, match="duplicate"):
        prepare_tess(tess_factory(time=[1, 1, 2]), tic_id="1", config=config)
    with pytest.raises(DataValidationError, match="mismatch"):
        prepare_tess(tess_factory(), tic_id="2", config=config)
    curve, audit = prepare_tess(tess_factory(n=8000), tic_id="1", config=config)
    assert "retained raw baseline shorter than 24 days" in basic_qc_reasons(curve, audit, config)


@pytest.mark.parametrize(
    "unit", ["BJD - 2457000, days", "BJD - 2457000, seconds", "BJD - 2400000, days"]
)
def test_documented_btjd_label_requires_matching_explicit_day_metadata(tess_factory, unit):
    config = load_config(Path("configs/protocol_v1.toml"))
    with fits.open(io.BytesIO(tess_factory()), memmap=False) as hdus:
        hdus[1].header["TUNIT1"] = unit
        hdus[0].header["DATA_REL"] = 42
        buffer = io.BytesIO()
        hdus.writeto(buffer)
    if unit != "BJD - 2457000, days":
        with pytest.raises(DataValidationError, match="units"):
            prepare_tess(buffer.getvalue(), tic_id="1", config=config)
    else:
        curve, audit = prepare_tess(buffer.getvalue(), tic_id="1", config=config)
        assert curve.original_end_days == pytest.approx(25)
        assert curve.time_metadata.absolute_epoch == 2458325
        assert audit["column_units"]["TIME"] == unit
        assert audit["source_data_release"] == 42
