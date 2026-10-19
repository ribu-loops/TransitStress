"""Default tests cannot perform network operations, including library fallbacks."""

import socket

import pytest
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
