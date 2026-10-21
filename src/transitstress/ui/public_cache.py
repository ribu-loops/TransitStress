"""Bounded cache of validated public evidence only; no upload/job inputs."""

import hashlib
from pathlib import Path

import streamlit as st

from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash
from transitstress.ui import bundle


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
    adapter_id = canonical_hash(
        {
            "validator": hashlib.sha256(Path(bundle.__file__).read_bytes()).hexdigest(),
            "cache": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    return validated_public(str(bundle.BUNDLE.resolve()), canonical_hash(meta), members, adapter_id)
