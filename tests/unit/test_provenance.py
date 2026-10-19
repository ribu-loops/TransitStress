"""Expected byte encodings are manually specified, not produced by tested helpers."""

import hashlib
import struct

import numpy as np
import pytest

from transitstress.errors import IdentityError
from transitstress.provenance import canonical_hash, canonical_json, derive_seed, numeric_hash


def test_canonical_json_known_bytes():
    expected = b'{"a":[true,null,0.75],"z":"TESS"}'
    assert canonical_json({"z": "TESS", "a": [True, None, 0.75]}) == expected
    assert (
        canonical_hash({"a": [True, None, 0.75], "z": "TESS"})
        == hashlib.sha256(expected).hexdigest()
    )
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 1.0})


def test_numeric_digest_manual_framing():
    # One vector [1,2] encoded as two IEEE-754 little-endian doubles.
    metadata = b"null"
    header = b'{"dtype":"<f8","name":"flux","shape":[2]}'
    payload = bytes.fromhex("000000000000f03f0000000000000040")
    framed = b"transitstress-numeric-v1\0"
    for item in (metadata, header, payload):
        framed += struct.pack("<Q", len(item)) + item
    assert numeric_hash({"flux": np.array([1.0, 2.0])}) == hashlib.sha256(framed).hexdigest()


def test_content_order_shape_endian_mask_and_metadata():
    values = np.array([[1.0, 2.0], [3.0, 4.0]])
    original = values.copy()
    a = numeric_hash({"flux": values, "keep": np.array([True, False])}, {"b": 2, "a": 1})
    b = numeric_hash(
        {"keep": np.array([1, 0], dtype="u1"), "flux": np.asfortranarray(values.astype(">f8"))},
        {"a": 1, "b": 2},
    )
    assert a == b
    assert a != numeric_hash(
        {"flux": values.ravel(), "keep": np.array([True, False])}, {"a": 1, "b": 2}
    )
    assert a != numeric_hash(
        {"flux": values + 1, "keep": np.array([True, False])}, {"a": 1, "b": 2}
    )
    np.testing.assert_array_equal(values, original)
    assert a != numeric_hash({"renamed": values, "keep": np.array([True, False])}, {"a": 1, "b": 2})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), np.array([1]), {1: "bad"}, object()])
def test_bad_metadata_is_controlled(value):
    with pytest.raises(IdentityError):
        canonical_json(value)


@pytest.mark.parametrize(
    "values", [[float("nan")], [float("inf")], [1 + 2j], ["1"], np.array([2], dtype="u1")]
)
def test_nonfinite_or_unsafe_array_types(values):
    with pytest.raises(IdentityError):
        numeric_hash({"x": np.asarray(values)})


def test_seed_reference_bytes_and_replicate_separation():
    reference = b'{"curve_id":"fixture","master_seed":7,"purpose":"random_missing"}'
    expected = int.from_bytes(hashlib.sha256(reference).digest()[:8], "little")
    assert derive_seed(7, "fixture", "random_missing") == expected
    assert derive_seed(7, "fixture", "gaussian", 0) != derive_seed(7, "fixture", "gaussian", 1)
    for seed in (-1, True, 2**64):
        with pytest.raises(IdentityError):
            derive_seed(seed, "fixture", "random_missing")


def test_inexact_integer_and_ragged_hash_inputs_are_controlled():
    with pytest.raises(IdentityError):
        numeric_hash({"counts": np.array([2**53 + 1], dtype="i8")})
    with pytest.raises(IdentityError):
        numeric_hash({"ragged": [[1], [1, 2]]})
