"""Forged NPY metadata must be rejected before any numeric payload allocation."""

import io
import math
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from scripts.t04_arrays import load_arrays
from transitstress.acquisition import source_sha256
from transitstress.errors import DataValidationError


def small_members() -> dict[str, bytes]:
    """Independent small archive; no large array is constructed for any case."""
    arrays = {
        key: np.arange(5, dtype=float) for key in ("time_days", "flux", "flux_err", "period_days")
    }
    for pipeline in ("rm_0p5d", "rm_1p5d"):
        for key in (
            "epoch_days",
            "duration_days",
            "depth",
            "depth_err",
            "depth_snr",
            "power",
            "flux",
            "flux_err",
            "trend",
            "neighbor_counts",
            "segment_ids",
        ):
            dtype = np.int64 if key in ("neighbor_counts", "segment_ids") else np.float64
            arrays[f"{pipeline}_{key}"] = np.arange(5, dtype=dtype)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def forged_npy(*, shape=(5,), descr="<f8", order=False, payload_bytes=40, version=(1, 0)):
    """Write literal bounded header text and at most 80 harmless payload bytes."""
    header = repr({"descr": descr, "fortran_order": order, "shape": shape}).encode("ascii") + b"\n"
    if version == (1, 0):
        prefix = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header))
    else:
        prefix = b"\x93NUMPY" + bytes(version) + struct.pack("<I", len(header))
    assert len(header) < 512 and payload_bytes <= 80
    return prefix + header + b"\0" * payload_bytes


def write_case(tmp_path, members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    content = buffer.getvalue()
    (tmp_path / "case.npz").write_bytes(content)
    return source_sha256(content)


class AllocationReached(AssertionError):
    pass


def test_report_billion_element_shape_never_reaches_allocation(tmp_path, monkeypatch):
    members = small_members()
    members["time_days.npy"] = forged_npy(shape=(1_000_000_000,))
    checksum = write_case(tmp_path, members)
    requests = []

    class GuardedNumpy:
        def __getattr__(self, name):
            return getattr(np, name)

        def ndarray(self, shape, *args, **kwargs):
            count = int(shape) if np.isscalar(shape) else math.prod(shape)
            requests.append(count)
            if count > 50000:
                raise AllocationReached(f"Stopped before allocation of {count} elements")
            return np.ndarray(shape, *args, **kwargs)

    monkeypatch.setitem(np.lib.format.read_array.__globals__, "numpy", GuardedNumpy())
    with pytest.raises(DataValidationError, match="numeric schema"):
        load_arrays(tmp_path, "case.npz", checksum)
    assert requests == []


@pytest.mark.parametrize("member", ["time_days.npy", "rm_1p5d_segment_ids.npy"])
@pytest.mark.parametrize(
    "header",
    [
        {"shape": (1_000_000_000,)},
        {"shape": (10**100,)},
        {"shape": (-5,)},
        {"shape": ()},
        {"shape": (5, 1)},
        {"shape": (4,)},
        {"shape": (6,), "payload_bytes": 48},
        {"descr": "<f4", "payload_bytes": 20},
        {"descr": "|O"},
        {"descr": "S1000000000"},
        {"descr": [("a", "<f8")]},
        {"payload_bytes": 32},
        {"payload_bytes": 48},
        {"order": "False"},
        {"version": (3, 0)},
        {"version": (9, 0)},
    ],
)
def test_all_headers_preflight_before_any_numpy_payload_loading(
    tmp_path, monkeypatch, member, header
):
    members = small_members()
    options = {"descr": "<i8" if member.endswith("segment_ids.npy") else "<f8", **header}
    members[member] = forged_npy(**options)
    checksum = write_case(tmp_path, members)

    def forbidden(*args, **kwargs):
        raise AssertionError("NumPy payload loader reached before rejecting forged header")

    monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(DataValidationError, match="numeric schema"):
        load_arrays(tmp_path, "case.npz", checksum)


@pytest.mark.parametrize("version", [(1, 0), (2, 0)])
def test_valid_numeric_versions_preserve_values_and_types(tmp_path, version):
    members = small_members()
    # Both storage-order booleans are meaningful supported NPY metadata.
    members["time_days.npy"] = forged_npy(version=version, order=True, descr=">f8")
    checksum = write_case(tmp_path, members)
    arrays = load_arrays(tmp_path, "case.npz", checksum)
    np.testing.assert_array_equal(arrays["time_days"], np.zeros(5))
    assert arrays["time_days"].dtype == np.dtype(">f8")
    assert arrays["rm_1p5d_segment_ids"].dtype == np.dtype("int64")


def test_existing_real_artifact_compatible_without_modification():
    root = Path("reports/t04_sanity")
    before = (root / "sanity_arrays.npz").read_bytes()
    arrays = load_arrays(root, "sanity_arrays.npz", source_sha256(before))
    assert len(arrays["time_days"]) == 3756
    assert len(arrays["period_days"]) == 23009
    assert arrays["period_days"][0] == 0.75
    assert int(arrays["rm_0p5d_neighbor_counts"].min()) == 34
    assert (root / "sanity_arrays.npz").read_bytes() == before


@pytest.mark.parametrize(
    "raw",
    [
        b"not an npy header",
        b"\x93NUMPY\x01\x00\xff\xff" + b" " * 520,
        b"\x93NUMPY\x02\x00\xff\xff\xff\xff" + b" " * 20,
        b"\x93NUMPY\x01\x00\x64\x00" + b"short",
        b"\x93NUMPY\x01\x00\x02\x00{\n" + b"\0" * 40,
        b"\x93NUMPY\x01\x00\x00\x00" + b"\0" * 40,
    ],
)
def test_malformed_header_length_or_syntax_rejected_before_payload(tmp_path, monkeypatch, raw):
    members = small_members()
    members["time_days.npy"] = raw
    checksum = write_case(tmp_path, members)

    def forbidden(*args, **kwargs):
        raise AssertionError("Malformed metadata must not reach NumPy payload loading")

    monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(DataValidationError, match="numeric schema"):
        load_arrays(tmp_path, "case.npz", checksum)
