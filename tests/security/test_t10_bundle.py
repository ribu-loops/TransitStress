"""Offline integrity/export/identity checks with no evaluation or acquisition."""

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest

from transitstress.artifacts import numeric_read, read_results_csv
from transitstress.errors import DataValidationError
from transitstress.ui import bundle as bundle_module
from transitstress.ui import public_cache
from transitstress.ui.browser import summary_table
from transitstress.ui.bundle import BUNDLE, FILES, case_zip, counts, load_bundle

ROOT = Path(__file__).resolve().parents[2]


def test_complete_offline_bundle_matches_original_sources(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("bundle tried science or private cache")

    for name in (
        "transitstress.search.search_bls",
        "transitstress.experiment.load_split",
        "transitstress.workbench.single_case",
        "transitstress.acquisition.fetch",
    ):
        # fetch's concrete public name is not part of the bundle import boundary.
        if name.endswith(".fetch"):
            continue
        monkeypatch.setattr(name, forbidden)
    bundle = load_bundle()
    assert len(bundle.rows) == 390 and len(bundle.plan) == 195
    table = summary_table(bundle.rows, set(bundle.arrays))
    for source, displayed in zip(bundle.rows, table, strict=True):
        assert displayed["seed"] == (None if source["seed"] is None else str(source["seed"]))
    assert {
        kind: sum(r["case_kind"] == kind for r in bundle.rows)
        for kind in ("injection", "uninjected_real", "gaussian_null")
    } == {"injection": 270, "uninjected_real": 30, "gaussian_null": 90}
    assert (counts(bundle.rows, "rm_0p5d")["exact"], counts(bundle.rows, "rm_1p5d")["exact"]) == (
        79,
        74,
    )
    for name, record in bundle.manifest["files"].items():
        if "#" not in record["source"]:
            assert bundle.content[name] == (ROOT / record["source"]).read_bytes()
    assert all(r["coverage_eligible"] is None for r in bundle.rows if r["case_kind"] != "injection")


@pytest.mark.parametrize("name", sorted(FILES))
def test_every_member_corruption_rejected_before_selection(name, tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    root = tmp_path / "bundle"
    (root / name).write_bytes(b"corrupt")
    with pytest.raises(DataValidationError, match="missing or corrupt"):
        load_bundle(root)


def test_bundle_inventory_bounds_and_no_path_from_manifest(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    root = tmp_path / "bundle"
    meta = json.loads((root / "bundle.json").read_text())
    meta["files"]["../outside"] = meta["files"].pop("results.csv")
    (root / "bundle.json").write_text(json.dumps(meta))
    with pytest.raises(DataValidationError, match="missing or corrupt"):
        load_bundle(root)


def test_public_evidence_binds_pair_and_downloaded_numeric_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("T10_PRIVATE_CANARY", "canary-must-not-export-t10")
    bundle = load_bundle()
    for cid in [*bundle.arrays, bundle.rows[0]["case_id"]]:
        payload = case_zip(bundle, cid)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert all("/" not in n and "\\" not in n for n in archive.namelist())
            for name, checksum in json.loads(archive.read("checksums.json")).items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == checksum
            rows = json.loads(archive.read("results.json"))["rows"]
            assert rows == [r for r in bundle.rows if r["case_id"] == cid]
            assert len(rows) == 2
            csv = read_results_csv(archive.read("results.csv"))
            assert [r["period_days"] for r in csv] == [r["period_days"] for r in rows]
            if cid in bundle.arrays:
                (tmp_path / "arrays.npz").write_bytes(archive.read("arrays.npz"))
                record = json.loads(archive.read("numeric_record.json"))["numerical"]
                decoded = numeric_read(tmp_path, "arrays.npz", record)
                for key, value in decoded.items():
                    np.testing.assert_array_equal(value, bundle.arrays[cid][key])
            else:
                assert b"SUMMARY ONLY" in archive.read("README.txt")
                assert "arrays.npz" not in archive.namelist()
            assert b"canary-must-not-export-t10" not in payload
    with pytest.raises(DataValidationError, match="case ID"):
        case_zip(bundle, "../../private")
    # No shared cache: mutations to one load cannot change another session's result.
    other = load_bundle()
    first_id = next(iter(bundle.arrays))
    bundle.rows[0]["period_days"] = 999
    bundle.arrays[first_id]["time_days"][0] = 999
    assert other.rows[0]["period_days"] != 999
    assert other.arrays[first_id]["time_days"][0] != 999


def test_semantic_row_corruption_detected_even_with_updated_transport_hash(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    root = tmp_path / "bundle"
    rows = json.loads((root / "results.json").read_bytes())
    rows["rows"][0]["run_id"] = "0" * 64
    data = json.dumps(rows).encode()
    (root / "results.json").write_bytes(data)
    manifest = json.loads((root / "bundle.json").read_bytes())
    manifest["files"]["results.json"].update(
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
    (root / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(DataValidationError, match="missing or corrupt"):
        load_bundle(root)


def test_public_cache_copies_and_revalidates_every_content_identity(monkeypatch, tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    root = tmp_path / "bundle"
    monkeypatch.setattr(bundle_module, "BUNDLE", root)
    public_cache.validated_public.clear()
    first = public_cache.public_bundle()
    first.rows[0]["period_days"] = 999
    assert public_cache.public_bundle().rows[0]["period_days"] != 999
    # A prior good cache entry must never conceal a corrupt member, selected or not.
    original = (root / "example3.npz").read_bytes()
    (root / "example3.npz").write_bytes(b"corrupt")
    with pytest.raises(DataValidationError, match="missing or corrupt"):
        public_cache.public_bundle()
    (root / "example3.npz").write_bytes(original)
    # Even changed content with a matching new transport record must miss the old
    # cache and undergo semantic validation rather than return the previous run.
    data = json.loads((root / "results.json").read_bytes())
    data["rows"][0]["run_id"] = "0" * 64
    raw = json.dumps(data).encode()
    (root / "results.json").write_bytes(raw)
    meta = json.loads((root / "bundle.json").read_bytes())
    meta["files"]["results.json"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / "bundle.json").write_text(json.dumps(meta))
    with pytest.raises(DataValidationError, match="missing or corrupt"):
        public_cache.public_bundle()
    public_cache.validated_public.clear()


def test_imported_validator_change_cannot_reuse_warm_public_cache(monkeypatch):
    public_cache.validated_public.clear()
    try:
        assert len(public_cache.public_bundle().rows) == 390
        calls = []

        def reject(*args, **kwargs):
            calls.append(True)
            raise DataValidationError("changed validator rejects")

        # Report reproduction: same artifact identity, different live validator.
        monkeypatch.setattr(bundle_module, "validate_rows", reject)
        with pytest.raises(DataValidationError) as rejected:
            public_cache.public_bundle()
        assert str(rejected.value.__cause__) == "changed validator rejects"
        assert calls == [True]
    finally:
        public_cache.validated_public.clear()


@pytest.mark.parametrize("module", ["artifacts", "provenance", "acquisition", "models", "errors"])
def test_validator_dependency_source_change_revalidates(monkeypatch, module):
    """Simulate changed disk bytes without editing protected scientific source."""
    original_read = Path.read_bytes
    target = ROOT / f"src/transitstress/{module}.py"
    original_load = bundle_module.load_bundle
    calls = []

    def observed_load(*args, **kwargs):
        calls.append(True)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(bundle_module, "load_bundle", observed_load)
    public_cache.validated_public.clear()
    try:
        public_cache.public_bundle()
        public_cache.public_bundle()
        assert len(calls) == 1

        def changed_read(path):
            raw = original_read(path)
            return raw + b"\n# changed validator source\n" if path == target else raw

        monkeypatch.setattr(Path, "read_bytes", changed_read)
        public_cache.public_bundle()
        assert len(calls) == 2
    finally:
        public_cache.validated_public.clear()


def test_changed_nested_validator_schema_invalidates_cache(monkeypatch):
    from transitstress import artifacts

    public_cache.validated_public.clear()
    try:
        public_cache.public_bundle()
        # In-place mutation keeps dict identity; a new schema still must validate.
        monkeypatch.setitem(artifacts.CSV_ENUMS, "split", ("development",))
        with pytest.raises(DataValidationError):
            public_cache.public_bundle()
    finally:
        public_cache.validated_public.clear()
