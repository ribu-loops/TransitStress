"""Safe evidence boundaries, including late forged NPY headers before any load."""

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest

from tests.unit.test_t04_science import curve
from transitstress import acquisition as acq
from transitstress.artifacts import (
    CSV_ENUMS,
    CSV_HASHES,
    CSV_NUMBERS,
    json_read,
    json_write,
    numeric_read,
    numeric_write,
    read_results_csv,
    results_csv,
)
from transitstress.config import load_config
from transitstress.errors import DataValidationError, IdentityError
from transitstress.workbench import local_spec, single_case


def row():
    result = {key: "a" * 64 for key in CSV_HASHES}
    result.update({key: values[0] for key, values in CSV_ENUMS.items()})
    result.update(dict.fromkeys(CSV_NUMBERS, None))
    result.update(
        depth=-0.002,
        retention_ratio={"value": -1.5, "reason": None},
        seed=2**64 - 1,
        reason="=HARMLESS()",
        curve_id="</script><img src=x>",
    )
    return result


def test_csv_numeric_projection_roundtrip_preserves_negative_null_and_uint64():
    content = results_csv([row()])
    assert b"HARMLESS" not in content and b"script" not in content
    parsed = read_results_csv(content)[0]
    assert parsed["depth"] == -0.002 and parsed["retention_ratio"] == -1.5
    assert parsed["seed"] == 2**64 - 1 and parsed["period_days"] is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "=HARMLESS()"),
        ("case_id", "../escape"),
        ("depth", "=HARMLESS()"),
        ("depth", float("nan")),
        ("depth", float("inf")),
    ],
)
def test_csv_rejects_text_and_nonfinite(key, value):
    item = row() | {key: value}
    with pytest.raises((DataValidationError, IdentityError)):
        results_csv([item])


def test_numeric_and_json_roundtrip_units_provenance_nulls(tmp_path):
    data = dict(
        time_days=np.array([0.0, 1.0, 3.0]),
        flux=np.array([1.0, -0.5, 2.0]),
        keep=np.array([True, False, True]),
    )
    record = numeric_write(tmp_path, "evidence.npz", data)
    restored = numeric_read(tmp_path, "evidence.npz", record)
    for name, expected in data.items():
        np.testing.assert_array_equal(restored[name], expected)
    metadata = dict(
        units=dict(
            time="days", flux="dimensionless", absolute_epoch=None, epoch_reason="unknown_csv_epoch"
        ),
        producing_commit="a" * 40,
        resumes=["b" * 40],
        ratio=dict(value=None, reason="insufficient_points"),
        numerical=record,
    )
    json_write(tmp_path, "evidence.json", metadata)
    assert json_read(tmp_path, "evidence.json") == metadata
    # Container bytes and numerical content have distinct hashes.
    assert record["transport_sha256"] != record["numeric_hash"]


@pytest.mark.parametrize(
    "shape,dtype",
    [
        ((10**9,), "<f8"),
        ((True,), "<f8"),
        ((3, 3), "<f8"),
        ((4,), "<f8"),
        ((3,), "|O"),
        ((3,), ">f8"),
        ((3,), "<f4"),
        ((3,), "<c16"),
    ],
)
def test_all_headers_preflight_before_loader_even_when_bad_member_last(
    tmp_path, monkeypatch, shape, dtype
):
    valid = io.BytesIO()
    np.save(valid, np.array([1.0, 2.0, 3.0]), allow_pickle=False)
    forged = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        forged, dict(descr=dtype, fortran_order=False, shape=shape)
    )
    forged.write(b"\0" * 24)  # tiny payload regardless of enormous declared count
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("first.npy", valid.getvalue())
        z.writestr("last.npy", forged.getvalue())
    content = archive.getvalue()
    (tmp_path / "forged.npz").write_bytes(content)
    record = dict(
        transport_sha256=acq.source_sha256(content),
        numeric_hash="a" * 64,
        arrays={k: dict(length=3, dtype="<f8") for k in ("first", "last")},
    )
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("payload loader reached before all headers passed")

    monkeypatch.setattr(np, "load", forbidden)
    with pytest.raises(DataValidationError, match="schema"):
        numeric_read(tmp_path, "forged.npz", record)
    assert not calls


@pytest.mark.parametrize(
    "arrays",
    [
        dict(a=np.array([object()])),
        dict(a=np.ones(50001)),
        dict(a=np.ones((2, 2))),
        dict(a=np.array([np.nan])),
        {"../escape": np.ones(2)},
        dict(a=np.array([2], dtype=np.uint8)),
    ],
)
def test_numeric_resource_type_name_bounds_before_writer(tmp_path, monkeypatch, arrays):
    def forbidden(*args, **kwargs):
        raise AssertionError("writer allocated before input validation")

    monkeypatch.setattr(np, "savez_compressed", forbidden)
    with pytest.raises((DataValidationError, IdentityError)):
        numeric_write(tmp_path, "bounded.npz", arrays)


@pytest.mark.parametrize("name", ["../escape.json", "C:\\escape.json", "/escape.json", ".."])
def test_generated_output_names_cannot_escape(tmp_path, name):
    with pytest.raises(DataValidationError, match="unsafe"):
        json_write(tmp_path, name, {})


def test_duplicate_nonfinite_json_rejected(tmp_path):
    for content in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
        (tmp_path / "bad.json").write_text(content)
        with pytest.raises(DataValidationError):
            json_read(tmp_path, "bad.json")


@pytest.mark.parametrize(
    "changes",
    [
        dict(period_days=0.5),
        dict(depth_ppm=0),
        dict(depth_ppm=5001),
        dict(duration_hours=7),
        dict(epoch_fraction=1),
        dict(period_days=float("nan")),
    ],
)
def test_public_ranges_rejected_before_search(changes):
    settings = dict(period_days=2.0, depth_ppm=1000.0, duration_hours=3.0, epoch_fraction=0.25)
    with pytest.raises(DataValidationError):
        local_spec(curve(), **(settings | changes))


def test_csv_unit_confirmation_and_remote_path_before_search(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid CSV reached engine")

    monkeypatch.setattr("transitstress.workbench.CaseEngine", forbidden)
    config = load_config(Path("configs/protocol_v1.toml"))
    for args in (
        dict(csv_content=b"time_days,flux,flux_err\n0,1,.1\n"),
        dict(csv_path="https://example.invalid/data.csv"),
    ):
        with pytest.raises(DataValidationError):
            single_case(
                config,
                repository=Path.cwd(),
                output=tmp_path / "output",
                period_days=2.0,
                depth_ppm=1000.0,
                duration_hours=3.0,
                epoch_fraction=0.25,
                **args,
            )
