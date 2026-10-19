"""Default tests cannot perform network operations, including library fallbacks."""

import io
import socket

import numpy as np
import pytest
from astropy.io import fits
from astropy.utils import iers


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    iers.conf.auto_download = False

    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def tess_factory():
    """Controlled FITS fixture, explicitly NOT archive or measured TESS data."""

    def build(tic=1, n=18001, time=None, flux=None, error=None, quality=None, headers=None):
        t = 1325 + np.arange(n) / 720 if time is None else np.array(time, dtype=float)
        n = len(t)
        f = np.full(n, 1000.0) if flux is None else np.array(flux, dtype=float)
        e = np.full(n, 2.0) if error is None else np.array(error, dtype=float)
        q = np.zeros(n, dtype=np.int32) if quality is None else np.array(quality, dtype=np.int32)
        primary = fits.PrimaryHDU()
        for k, v in {
            "TELESCOP": "TESS",
            "SECTOR": 1,
            "TICID": tic,
            "ORIGIN": "NASA/Ames",
            "PROCVER": "spoc-5.0.0-test",
            "DATE": "2026-10-19T00:00:00",
        }.items():
            primary.header[k] = v
        table = fits.BinTableHDU.from_columns(
            [
                fits.Column(name="TIME", array=t, format="D", unit="d"),
                fits.Column(name="PDCSAP_FLUX", array=f, format="D", unit="e-/s"),
                fits.Column(name="PDCSAP_FLUX_ERR", array=e, format="D", unit="e-/s"),
                fits.Column(name="QUALITY", array=q, format="J"),
            ]
        )
        metadata = {
            "TIMESYS": "TDB",
            "TIMEUNIT": "d",
            "BJDREFI": 2457000,
            "BJDREFF": 0.0,
            "TIMEDEL": 120 / 86400,
        }
        metadata.update(headers or {})
        for k, v in metadata.items():
            table.header[k] = v
        result = io.BytesIO()
        fits.HDUList([primary, table]).writeto(result)
        return result.getvalue()

    return build
