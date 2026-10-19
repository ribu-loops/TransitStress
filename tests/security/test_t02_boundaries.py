"""T02 offline security regressions use harmless bytes and mocked identities only."""

from pathlib import Path

import pytest
from astropy.io import fits

from transitstress.acquisition import atomic_write, safe_file
from transitstress.archive import MAX_TOTAL, AcquisitionBudget, MastArchive, validate_endpoint
from transitstress.config import load_config
from transitstress.csv_input import parse_csv, read_local_csv
from transitstress.errors import DataValidationError
from transitstress.tess_input import prepare_tess


@pytest.mark.parametrize(
    "content",
    [
        b"time_days,flux,flux_err\n0,=SUM(1),1",
        b"time_days,flux,flux_err\n0,NaN,1",
        b"time_days,flux,flux_err\n0,1,1e-300\n25,1,1e-300",
        b"time_days,flux,flux_err,flux\n0,1,1,1",
        b"x" * (5242880 + 1),
    ],
    ids=["formula", "nan", "tiny_errors", "duplicate_headers", "oversized"],
)
def test_strict_csv_security(content):
    with pytest.raises(DataValidationError):
        parse_csv(
            content,
            confirm_days_and_linear_units=True,
            config=load_config(Path("configs/protocol_v1.toml")),
        )


@pytest.mark.parametrize(
    "path", ["http://169.254.169.254/a.csv", "s3://bucket/a.csv", "\\\\server\\a.csv"]
)
def test_local_csv_cannot_be_network_request(path):
    with pytest.raises(DataValidationError):
        read_local_csv(
            path,
            confirm_days_and_linear_units=True,
            config=load_config(Path("configs/protocol_v1.toml")),
        )


@pytest.mark.parametrize(
    "name", ["../escape", "..", "C:\\escape", "/absolute", "nested/name", "\\\\server\\share"]
)
def test_generated_cache_path_containment(tmp_path, name):
    with pytest.raises(DataValidationError):
        atomic_write(tmp_path, name, b"harmless")


def test_symlink_cache_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip(
            "OS cannot create symlinks; containment is also tested with mocked symlink status"
        )
    with pytest.raises(DataValidationError, match="symlink"):
        safe_file(link, "file.json")


def test_symlink_status_rejected_without_os_privilege(tmp_path, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == tmp_path or original(self))
    with pytest.raises(DataValidationError, match="symlink"):
        safe_file(tmp_path, "file.json")


def test_windows_junction_status_rejected(tmp_path, monkeypatch):
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda self: self == tmp_path or original(self))
    with pytest.raises(DataValidationError, match="junction"):
        safe_file(tmp_path, "file.json")


def test_default_archive_offline_guard_and_budgets():
    with pytest.raises(DataValidationError, match="offline"):
        MastArchive(offline=True).query("Mast.Caom.Products", {"obsid": "1"})
    with pytest.raises(DataValidationError, match="30"):
        AcquisitionBudget(downloaded_before=30).begin_download(1)
    with pytest.raises(DataValidationError, match="1 GiB"):
        AcquisitionBudget(bytes_received=MAX_TOTAL).add(1)
    for url in (
        "https://127.0.0.1/",
        "https://169.254.169.254/",
        "https://mast.stsci.edu.attacker.test/",
    ):
        with pytest.raises(DataValidationError):
            validate_endpoint(url)


def test_fits_compression_and_declared_table_size_before_arrays(tess_factory):
    config = load_config(Path("configs/protocol_v1.toml"))
    with pytest.raises(DataValidationError, match="uncompressed FITS"):
        prepare_tess(b"\x1f\x8b" + b"harmless" * 20, tic_id="1", config=config)
    content = tess_factory(n=2)
    index = content.index(b"NAXIS2  =")
    oversized = (
        content[:index]
        + fits.Card("NAXIS2", 99999999).image.encode("ascii")
        + content[index + 80 :]
    )
    with pytest.raises(DataValidationError, match="table size"):
        prepare_tess(oversized, tic_id="1", config=config)
