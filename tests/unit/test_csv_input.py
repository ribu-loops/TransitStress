from pathlib import Path

import numpy as np
import pytest

from transitstress.config import load_config
from transitstress.csv_input import parse_csv, read_local_csv
from transitstress.errors import DataValidationError


@pytest.fixture
def config():
    return load_config(Path("configs/protocol_v1.toml"))


def csv_bytes(n=2501, start=2457000, span=25, extra=False):
    header = "time_days,flux,flux_err" + (",unused" if extra else "")
    return (
        header
        + "\n"
        + "\n".join(
            f"{t:.10f},12,2" + (",=HYPERLINK(1)" if extra else "")
            for t in np.linspace(start, start + span, n)
        )
    ).encode()


def test_valid_unknown_absolute_epoch_sorted_support_and_notice(config):
    source = b"\xef\xbb\xbf" + csv_bytes(extra=True)
    result = parse_csv(source, confirm_days_and_linear_units=True, config=config)
    assert result.input_rows == 2501
    assert result.source_start_days == 2457000
    assert result.source_end_days == 2457025
    assert result.curve.time_metadata.absolute_epoch is None
    assert result.curve.time_metadata.time_format == "unknown"
    assert result.curve.original_end_days == 25
    assert result.curve.flux == pytest.approx(np.ones(2501))
    assert result.curve.flux_err == pytest.approx(np.full(2501, 1 / 6))
    assert sum(result.curve.support_counts) == 2501
    assert result.notices and "Ignored 1" in result.notices[0]


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xff",
        b"\x1f\x8b",
        b"time_days,flux,flux_err\n0,1,1\0",
        b"time_days,flux,flux,flux_err\n0,1,1,1",
        b"time_days,flux\n0,1",
        b"time_days,flux,flux_err\n0,1",
        b"time_days,flux,flux_err\n0,1,1,extra",
        b'time_days,flux,flux_err\n"0,1,1',
        b"time_days,flux,flux_err\n0,1,1\n\n",
    ],
)
def test_malformed_csv(config, content):
    with pytest.raises(DataValidationError):
        parse_csv(content, confirm_days_and_linear_units=True, config=config)


@pytest.mark.parametrize(
    "token", ["", "NaN", "Inf", "-Infinity", "=1+2", "@SUM(A1)", "0x2", "1e9999", "1_000"]
)
def test_bad_tokens_rejected_before_preparation(config, token):
    with pytest.raises(DataValidationError, match="numeric|range"):
        parse_csv(
            f"time_days,flux,flux_err\n0,{token},1".encode(),
            confirm_days_and_linear_units=True,
            config=config,
        )


@pytest.mark.parametrize("span", [23.99, 40.01])
def test_baseline_limits(config, span):
    with pytest.raises(DataValidationError, match="baseline"):
        parse_csv(csv_bytes(span=span), confirm_days_and_linear_units=True, config=config)


@pytest.mark.parametrize("span", [24, 40])
def test_baseline_endpoints(config, span):
    assert (
        parse_csv(
            csv_bytes(span=span), confirm_days_and_linear_units=True, config=config
        ).curve.original_end_days
        == span
    )


def test_size_row_bin_and_duplicate_limits(config):
    with pytest.raises(DataValidationError, match="bytes"):
        parse_csv(b"x" * (5242880 + 1), confirm_days_and_linear_units=True, config=config)
    with pytest.raises(DataValidationError, match="rows"):
        parse_csv(csv_bytes(n=50001), confirm_days_and_linear_units=True, config=config)
    with pytest.raises(DataValidationError, match="bins"):
        parse_csv(csv_bytes(n=1999), confirm_days_and_linear_units=True, config=config)
    source = csv_bytes() + b"\n2457000,12,2"
    with pytest.raises(DataValidationError, match="duplicate"):
        parse_csv(source, confirm_days_and_linear_units=True, config=config)


def test_units_must_be_confirmed(config):
    with pytest.raises(DataValidationError, match="units"):
        parse_csv(csv_bytes(), confirm_days_and_linear_units=False, config=config)


@pytest.mark.parametrize(
    "path",
    [
        "https://mast.stsci.edu/file.csv",
        "http://127.0.0.1/a.csv",
        "s3://bucket/a.csv",
        "file:///a.csv",
        "\\\\server\\a.csv",
        "//server/a.csv",
    ],
)
def test_no_uri_or_unc_open(config, path, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("URI must be rejected before filesystem access")

    monkeypatch.setattr("pathlib.Path.open", forbidden)
    with pytest.raises(DataValidationError, match="remote/URI"):
        read_local_csv(path, confirm_days_and_linear_units=True, config=config)


def test_explicit_local_path(config, tmp_path):
    file = tmp_path / "local.csv"
    file.write_bytes(csv_bytes())
    result = read_local_csv(file, confirm_days_and_linear_units=True, config=config)
    assert result.input_rows == 2501
    with pytest.raises(DataValidationError, match="regular local"):
        read_local_csv(tmp_path, confirm_days_and_linear_units=True, config=config)
