"""Versioned canonical content encodings, independent of paths, ZIPs and Git HEAD."""

import hashlib
import json
import math
import struct
from collections.abc import Mapping
from dataclasses import fields, is_dataclass

import numpy as np
from numpy.typing import NDArray

from transitstress.errors import IdentityError


def _plain(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise IdentityError("metadata keys: invalid type; use strings")
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise IdentityError("metadata: unsupported/nonfinite value; use finite JSON data")


def canonical_json(value: object) -> bytes:
    """UTF-8, sorted keys, compact separators, ASCII escapes, finite JSON only.

    Integer and floating values remain distinct. No repr(), path or timestamp coercion.
    """
    try:
        return json.dumps(
            _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise IdentityError(
            "metadata: cannot encode canonical JSON; supply valid JSON data"
        ) from error


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def numeric_hash(arrays: Mapping[str, NDArray], metadata: object = None) -> str:
    """Frame names/shapes/types and C-order LE float64 or explicit uint8 mask bytes.

    This is a content identity, not an NPZ transport checksum. Caller arrays are untouched.
    Boolean and uint8 arrays share the mask encoding (only 0/1 accepted).
    """
    digest = hashlib.sha256(b"transitstress-numeric-v1\0")

    def frame(payload: bytes) -> None:
        digest.update(struct.pack("<Q", len(payload)))
        digest.update(payload)

    frame(canonical_json(metadata))
    if any(type(name) is not str or not name for name in arrays):
        raise IdentityError("arrays: invalid name; use nonempty string names")
    for name in sorted(arrays):
        try:
            source = np.asarray(arrays[name])
        except (TypeError, ValueError) as error:
            raise IdentityError("arrays: invalid shape/type; supply numeric arrays") from error
        if source.dtype.kind not in "biuf" or not np.all(np.isfinite(source)):
            raise IdentityError("arrays: invalid dtype/value; use finite real numeric arrays")
        if source.dtype.kind in "iu" and np.any((source > 2**53) | (source < -(2**53))):
            raise IdentityError("arrays: inexact integer; use integers representable in float64")
        mask = source.dtype == np.dtype(bool) or source.dtype == np.dtype("uint8")
        if mask and np.any((source != 0) & (source != 1)):
            raise IdentityError("mask: invalid value; use only 0/1")
        with np.errstate(over="ignore", invalid="ignore"):
            array = np.array(source, dtype="u1" if mask else "<f8", order="C", copy=True)
        if not np.all(np.isfinite(array)):
            raise IdentityError("arrays: float64 overflow; supply representable finite values")
        frame(canonical_json({"name": name, "shape": list(array.shape), "dtype": array.dtype.str}))
        frame(array.tobytes(order="C"))
    return digest.hexdigest()


def derive_seed(master_seed: int, curve_id: str, purpose: str, replicate: int | None = None) -> int:
    """SCIENCE 4.3: first eight digest bytes interpreted as unsigned little endian."""
    if type(master_seed) is not int or not 0 <= master_seed < 2**64:
        raise IdentityError("master_seed: invalid; supply an unsigned 64-bit integer")
    if not isinstance(curve_id, str) or not curve_id or not isinstance(purpose, str) or not purpose:
        raise IdentityError("seed identity: missing curve/purpose; supply nonempty strings")
    if replicate is not None and (type(replicate) is not int or replicate < 0):
        raise IdentityError("replicate: invalid; supply a nonnegative integer or null")
    identity = {"master_seed": master_seed, "curve_id": curve_id, "purpose": purpose}
    if replicate is not None:
        identity["replicate"] = replicate
    return int.from_bytes(hashlib.sha256(canonical_json(identity)).digest()[:8], "little")
