"""Independent bounded NPY fixtures; no FITS preparation or science execution."""

import hashlib
import io
import struct
import zipfile
from dataclasses import replace

import numpy as np
import pytest

from transitstress.acquisition import save_prepared
from transitstress.errors import DataValidationError
from transitstress.models import PreparedCurve, TimeMetadata


def curve():
    return PreparedCurve(
        "synthetic-wsl",
        np.array([0.0, 1.0, 2.0]),
        np.array([1.0, 1.25, 0.75]),
        np.array([0.01, 0.02, 0.03]),
        0.0,
        2.0,
        0.0,
        np.array([1, 2, 3]),
        TimeMetadata(None, "unknown", "unknown", "days", "fixture", 1.0),
        "0" * 64,
    )


def npy(values, dtype):
    # NPY v1.0 framing written independently, including exact canonical header padding.
    header = f"{{'descr': '{dtype}', 'fortran_order': False, 'shape': (3,), }}"
    header += " " * (64 - (10 + len(header) + 1) % 64) + "\n"
    return (
        b"\x93NUMPY\x01\x00"
        + struct.pack("<H", len(header))
        + header.encode()
        + struct.pack("<3" + ("d" if dtype == "<f8" else "q"), *values)
    )


def members():
    return {
        "time_days.npy": npy([0, 1, 2], "<f8"),
        "flux.npy": npy([1, 1.25, 0.75], "<f8"),
        "flux_err.npy": npy([0.01, 0.02, 0.03], "<f8"),
        "support_counts.npy": npy([1, 2, 3], "<i8"),
    }


def pack(items, system=0, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name, data in items:
            info = zipfile.ZipInfo(name)
            info.create_system = system
            info.compress_type = compression
            z.writestr(info, data)
    return stream.getvalue()


@pytest.mark.parametrize("system", [0, 3])
@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_metadata_only_changes_preserve_exact_existing_transport(
    tmp_path, monkeypatch, system, compression
):
    c = curve()
    original = pack(list(members().items()), system, compression)
    target = tmp_path / (c.content_hash + ".npz")
    target.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(np, "load", lambda *a, **k: pytest.fail("must not deserialize cache"))
    save_prepared(tmp_path, c)
    assert target.read_bytes() == original
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    generated = tmp_path / "new"
    save_prepared(generated, c)
    with zipfile.ZipFile(generated / target.name) as z:
        assert {n: z.read(n) for n in z.namelist()} == members()


@pytest.mark.parametrize(
    "change",
    [
        "ulp",
        "signed_zero",
        "dtype",
        "shape",
        "order",
        "object",
        "extra_payload",
        "short_payload",
        "duplicate",
        "missing",
        "path",
        "extra",
        "billion_shape",
        "oversize",
        "crc",
        "truncated",
    ],
)
def test_changed_headers_payloads_and_unsafe_containers_reject_preserving_failure(
    tmp_path, monkeypatch, change
):
    c = curve()
    values = members()
    if change == "ulp":
        data = bytearray(values["flux.npy"])
        data[128] ^= 1  # one mantissa bit; approximate equality must never pass
        values["flux.npy"] = bytes(data)
    elif change == "signed_zero":
        values["time_days.npy"] = values["time_days.npy"][:128] + struct.pack("<3d", -0.0, 1.0, 2.0)
    elif change in {"dtype", "shape", "order", "object", "billion_shape"}:
        before, after = {
            "dtype": (b"<f8", b">f8"),
            "shape": (b"(3,)", b"(1,3)"),
            "order": (b"False", b"True "),
            "object": (b"<f8", b"|O "),
            "billion_shape": (b"(3,)", b"(1000000000,)"),
        }[change]
        values["flux.npy"] = values["flux.npy"].replace(before, after)
    elif change == "extra_payload":
        values["flux.npy"] += b"x"
    elif change == "short_payload":
        values["flux.npy"] = values["flux.npy"][:-1]
    elif change == "missing":
        values.pop("flux.npy")
    elif change == "path":
        values["../flux.npy"] = values.pop("flux.npy")
    elif change == "extra":
        values["unexpected.npy"] = b"extra"
    items = list(values.items())
    if change == "duplicate":
        items.append(items[0])
    raw = pack(items)
    if change == "oversize":
        raw += b"x" * (4 * 1024**2)
    elif change == "crc":
        raw = raw.replace(struct.pack("<3d", 1.0, 1.25, 0.75), struct.pack("<3d", 1.0, 1.5, 0.75))
    elif change == "truncated":
        raw = raw[:-24]
    target = tmp_path / (c.content_hash + ".npz")
    target.write_bytes(raw)
    monkeypatch.setattr(np, "load", lambda *a, **k: pytest.fail("untrusted allocation reached"))
    with pytest.raises(DataValidationError, match="prepared cache"):
        save_prepared(tmp_path, c)
    assert target.read_bytes() == raw
    assert len(list(tmp_path.iterdir())) == 1


def test_provenance_changes_do_not_reuse_previous_identity(tmp_path):
    c = curve()
    save_prepared(tmp_path, c)
    old = (tmp_path / (c.content_hash + ".npz")).read_bytes()
    revised = replace(c, source_sha256="1" * 64)
    assert revised.content_hash != c.content_hash
    save_prepared(tmp_path, revised)
    assert (tmp_path / (c.content_hash + ".npz")).read_bytes() == old
    assert len(list(tmp_path.glob("*.npz"))) == 2


@pytest.mark.parametrize(
    "variant", ["broken_deflate", "unsupported_compression", "strong_encryption"]
)
def test_malformed_compressed_container_is_named_validation_failure(tmp_path, variant):
    compression = zipfile.ZIP_DEFLATED if variant == "broken_deflate" else zipfile.ZIP_STORED
    raw = bytearray(pack(list(members().items()), compression=compression))
    local = raw.index(b"PK\x03\x04")
    central = raw.index(b"PK\x01\x02")
    if variant == "broken_deflate":
        name_length, extra_length = struct.unpack_from("<HH", raw, local + 26)
        raw[local + 30 + name_length + extra_length] = 0xFF
    elif variant == "unsupported_compression":
        struct.pack_into("<H", raw, local + 8, 99)
        struct.pack_into("<H", raw, central + 10, 99)
    else:
        struct.pack_into("<H", raw, local + 6, 0x40)
        struct.pack_into("<H", raw, central + 8, 0x40)
    target = tmp_path / (curve().content_hash + ".npz")
    target.write_bytes(raw)
    with pytest.raises(DataValidationError, match="prepared cache"):
        save_prepared(tmp_path, curve())
    assert target.read_bytes() == raw
