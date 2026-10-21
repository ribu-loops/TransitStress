"""Bounded cache of validated public evidence only; no upload/job inputs."""

import hashlib
import json
import sys
from pathlib import Path
from types import FunctionType, ModuleType

import streamlit as st

from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash
from transitstress.ui import bundle


def validator_identity() -> str:
    """Current owned source/dependencies and live validation bindings, never data.

    Hash the entire package so an added/imported helper cannot escape an explicit
    short whitelist. Source bytes include constants/defaults; live bindings also
    invalidate reloads, monkeypatches and in-place code/schema changes. These are
    process-local memory-cache keys, not scientific or exported identities.
    """
    package = Path(__file__).resolve().parents[1]
    root = package.parents[1]
    files = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(package.rglob("*.py"))
        if "__pycache__" not in p.parts
    }
    for name in ("uv.lock", "pyproject.toml", ".python-version"):
        files[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()

    def function_key(value: FunctionType) -> tuple:
        return (
            id(value),
            id(value.__code__),
            hashlib.sha256(
                repr(
                    (value.__code__.co_code, value.__code__.co_consts, value.__code__.co_names)
                ).encode()
            ).hexdigest(),
            id(value.__defaults__),
            id(value.__kwdefaults__),
        )

    bindings = {}
    for name, module in sorted(tuple(sys.modules.items())):
        if not name.startswith("transitstress.") or not isinstance(module, ModuleType):
            continue
        for key, value in tuple(vars(module).items()):
            label = f"{name}.{key}"
            if isinstance(value, FunctionType):
                bindings[label] = function_key(value)
            elif isinstance(value, type) and value.__module__.startswith("transitstress"):
                bindings[label] = id(value)
                for member, method in tuple(vars(value).items()):
                    if isinstance(method, (staticmethod, classmethod)):
                        method = method.__func__
                    if isinstance(method, FunctionType):
                        bindings[f"{label}.{member}"] = function_key(method)
            elif key.isupper():
                try:
                    bindings[label] = json.dumps(value, sort_keys=True, allow_nan=False)
                except (TypeError, ValueError):
                    bindings[label] = id(value)
    return hashlib.sha256(
        json.dumps(
            dict(
                files=files,
                bindings=bindings,
                python=sys.version,
                dependencies=dict(numpy=bundle.np.__version__, streamlit=st.__version__),
            ),
            sort_keys=True,
        ).encode()
    ).hexdigest()


@st.cache_data(max_entries=2, show_spinner=False)
def validated_public(
    root: str, bundle_id: str, members: tuple[tuple[str, str], ...], adapter_id: str
) -> bundle.DemoBundle:
    """All arguments hashed; only trusted validation output is serialized in memory."""
    value = bundle.load_bundle(Path(root))
    actual = tuple((n, hashlib.sha256(v).hexdigest()) for n, v in sorted(value.content.items()))
    if value.fingerprint != bundle_id or actual != members:
        raise DataValidationError("Demo bundle changed during validation; restore it and restart.")
    return value


def public_bundle() -> bundle.DemoBundle:
    """Fixed packaged root only. Hash all members on EVERY rerender, even a cache hit.

    Missing/corrupt files cannot be hidden by a previous entry. Key includes all
    data, run/protocol/scientific identity bytes, and validator source. Streamlit
    returns a separate copy to each caller. Private local state never enters here.
    """
    meta, _ = bundle.transport(bundle.BUNDLE)
    members = tuple((name, record["sha256"]) for name, record in sorted(meta["files"].items()))
    adapter_id = validator_identity()
    return validated_public(str(bundle.BUNDLE.resolve()), canonical_hash(meta), members, adapter_id)
