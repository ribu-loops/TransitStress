"""Historical inventory runner regressions, plus unchanged shared product/QC checks.

Public fetch now uses the exact cohort; test_t02_cohort exercises that route.
The private legacy runner is retained to preserve earlier regression evidence.
"""

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from transitstress.acquisition import (
    _fetch_inventory_legacy as fetch,
)
from transitstress.acquisition import (
    acquire_inventory,
    cache_name,
    candidate_inventory,
    choose_product,
    read_json,
    reconcile_processing,
    require_final_manifest,
    write_json,
    write_selection_csv,
)
from transitstress.archive import AcquisitionBudget, MastArchive
from transitstress.config import load_config
from transitstress.errors import DataValidationError

SANITY = 261136679


def observation(tic, group=None, release=60000.0):
    group = tic + 1000 if group is None else group
    return {
        "obsid": group,
        "obs_id": f"fixture-{group}",
        "target_name": str(tic),
        "obs_collection": "TESS",
        "provenance_name": "SPOC",
        "sequence_number": 1,
        "t_exptime": 120.0,
        "dataproduct_type": "timeseries",
        "dataRights": "PUBLIC",
        "t_obs_release": release,
    }


def product(tic, size, group=None, configuration="0120", version="spoc-5.0.0-test"):
    group = tic + 1000 if group is None else group
    filename = f"tess2018206045859-s0001-{tic:016d}-{configuration}-s_lc.fits"
    return {
        "obsID": group,
        "parent_obsid": group,
        "obs_id": f"fixture-{group}",
        "obs_collection": "TESS",
        "dataRights": "PUBLIC",
        "productType": "SCIENCE",
        "productFilename": filename,
        "dataURI": "mast:TESS/product/" + filename,
        "size": size,
        "prvversion": version,
    }


@pytest.fixture
def scenario(tess_factory, monkeypatch):
    # These historical runner fixtures predate cohort identity. Public validation
    # always enforces the cohort; retain the original shared field checks here.
    from transitstress.acquisition import _validate_manifest_fields

    monkeypatch.setattr("transitstress.acquisition.validate_manifest", _validate_manifest_fields)
    # Build provisional data before exercising final/frozen lifecycle behavior.
    config = replace(load_config(Path("configs/protocol_v1.toml")), frozen=False)
    # TIC 1 fails the 24-day criterion; seven later TICs pass. TIC 10 would be next.
    tics = [10, 8, 1, 3, SANITY, 2, 7, 6, 5, 4]
    observations = [observation(t) for t in tics]
    sources = {t: tess_factory(tic=t, n=16001 if t == 1 else 18001) for t in tics}
    products = {str(t + 1000): [product(t, len(sources[t]))] for t in tics}

    class Archive(MastArchive):
        def __init__(self, offline=False):
            super().__init__(AcquisitionBudget(), offline=offline)
            self.queries = []
            self.requested = []

        def query(self, service, params, *, pagesize=1000):
            assert not self.offline
            self.queries.append((service, params))
            if service == "Mast.Caom.Filtered" and params["columns"] == "COUNT_BIG(*)":
                return [{"Column1": len(observations)}]
            return copy.deepcopy(
                observations if service == "Mast.Caom.Filtered" else products[params["obsid"]]
            )

        def download(self, uri, declared_size):
            if self.offline:
                raise DataValidationError("archive: offline; restore the pinned cache")
            self.budget.begin_download(declared_size)
            self.requested.append(uri)
            tic = int(uri.split("-")[2])
            content = sources[tic]
            self.budget.add(len(content))
            return content

    return config, Archive, observations, products, sources


def test_selection_provisional_evidence_and_offline_repeat(tmp_path, scenario):
    config, Archive, _, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    archive = Archive()
    report = fetch(config, data_root=data, cache_root=cache, archive=archive)
    assert report["status"] == "provisional" and report["downloads"] == 9
    manifest = read_json(data, "manifest.json")
    assert [p["tic_id"] for p in manifest["products"]] == [
        str(SANITY),
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
    ]
    assert [p["role"] for p in manifest["products"]] == [
        "sanity",
        "development",
        "development",
        *(["evaluation"] * 5),
    ]
    state = read_json(data, "acquisition.json")
    assert [a["tic_id"] for a in state["attempts"]] == [
        str(SANITY),
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
    ]
    assert state["attempts"][1]["status"] == "basic_qc_excluded"
    assert state["attempts"][1]["product"]["qc"]["retained_rows"] == 16001
    assert state["attempts"][1]["reasons"] == ["retained raw baseline shorter than 24 days"]
    assert state["downloads_total"] == 9
    with pytest.raises(DataValidationError, match="T03"):
        require_final_manifest(manifest)
    before = (data / "manifest.json").read_bytes()
    second = Archive(offline=True)
    assert fetch(config, data_root=data, cache_root=cache, archive=second)["downloads"] == 0
    assert not second.queries and not second.requested
    assert before == (data / "manifest.json").read_bytes()
    write_selection_csv(data)
    assert "basic_qc_excluded" in (data / "selection.csv").read_text()


def test_pinned_final_clean_clone_reacquisition_no_query_no_manifest_change(tmp_path, scenario):
    config, Archive, _, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    fetch(config, data_root=data, cache_root=cache, archive=Archive())
    manifest = read_json(data, "manifest.json")
    # This is a MOCK final manifest; no real T03 support validation is claimed.
    manifest.update(status="final", support_validation="complete", roles_provisional=False)
    write_json(data, "manifest.json", manifest)
    before = (data / "manifest.json").read_bytes()
    clean_cache = tmp_path / "clean_clone_cache"
    archive = Archive()
    report = fetch(
        replace(config, frozen=True), data_root=data, cache_root=clean_cache, archive=archive
    )
    assert report["downloads"] == 8 and not archive.queries
    assert archive.requested == [p["data_uri"] for p in manifest["products"]]
    assert (data / "manifest.json").read_bytes() == before


def test_corrupt_missing_cache_offline_and_upstream_change(tmp_path, scenario):
    config, Archive, _, _, sources = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    fetch(config, data_root=data, cache_root=cache, archive=Archive())
    p = read_json(data, "manifest.json")["products"][0]
    source = cache / cache_name(p)
    original = source.read_bytes()
    source.write_bytes(b"corrupt")
    with pytest.raises(DataValidationError, match="corrupt"):
        fetch(config, data_root=data, cache_root=cache, archive=Archive(offline=True))
    assert source.read_bytes() == b"corrupt"
    source.unlink()
    with pytest.raises(DataValidationError, match="offline"):
        fetch(config, data_root=data, cache_root=cache, archive=Archive(offline=True))
    sources[SANITY] = original[:-1] + bytes([original[-1] ^ 1])
    with pytest.raises(DataValidationError, match="hash"):
        fetch(config, data_root=data, cache_root=cache, archive=Archive())
    assert not source.exists()
    assert read_json(cache, "last_fetch.json")["status"] == "failed"


def test_corrupt_prepared_cache_is_failure_not_reselection(tmp_path, scenario):
    config, Archive, _, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    fetch(config, data_root=data, cache_root=cache, archive=Archive())
    p = read_json(data, "manifest.json")["products"][0]
    snapshot = cache / (p["qc"]["prepared_hash"] + ".npz")
    snapshot.write_bytes(b"corrupt")
    archive = Archive()
    with pytest.raises(DataValidationError, match="prepared cache"):
        fetch(config, data_root=data, cache_root=cache, archive=archive)
    assert not archive.queries and not archive.requested
    assert snapshot.read_bytes() == b"corrupt"


def test_sort_dedupe_filter_release_and_filename_tie_break(scenario):
    config, _, observations, products, _ = scenario
    with pytest.raises(DataValidationError, match="duplicate obsid"):
        candidate_inventory(observations + [observations[0]], config.sanity.tic_id)
    inventory = candidate_inventory(
        observations + [observation(10, group=12345)], config.sanity.tic_id
    )
    assert [c["tic_id"] for c in inventory["candidates"]][:3] == ["1", "2", "3"]
    candidate = next(c for c in inventory["candidates"] if c["tic_id"] == "2")
    candidate["observations"].append(observation(2, group=99, release=60001.0))
    rows = products["1002"] + [product(2, 100, group=99, configuration="0121")]
    wrong = {**rows[0], "productFilename": "target_tp.fits"}
    with pytest.raises(DataValidationError, match="processing release"):
        choose_product(rows + [wrong], candidate)
    chosen, evidence = choose_product(rows[:1] + [wrong], candidate)
    assert chosen["observation_group_id"] == "1002"
    assert evidence[-1]["exclusion_reason"]
    tied = [product(2, 100, group=99, configuration=v) for v in ("0122", "0121")]
    with pytest.raises(DataValidationError, match="processing release"):
        choose_product(tied, candidate)
    conflict = copy.deepcopy(tied[1])
    conflict["size"] += 1
    with pytest.raises(DataValidationError, match="conflicting"):
        choose_product(tied + [conflict], candidate)


def test_failed_download_preserved_then_same_product_resumed(tmp_path, scenario):
    config, Archive, _, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    archive = Archive()

    def failure(uri, size):
        archive.budget.begin_download(size)
        raise DataValidationError("archive connection: timed out; retry pinned identity")

    archive.download = failure
    with pytest.raises(DataValidationError, match="timed out"):
        fetch(config, data_root=data, cache_root=cache, archive=archive)
    state = read_json(data, "acquisition.json")
    assert state["downloads_total"] == 1
    assert state["attempts"][0]["events"][0]["status"] == "failed"
    pinned = state["attempts"][0]["product"]
    resumed = Archive()
    fetch(config, data_root=data, cache_root=cache, archive=resumed)
    assert resumed.requested[0] == pinned["data_uri"]
    assert all(q[0] != "Mast.Caom.Filtered" for q in resumed.queries)
    state = read_json(data, "acquisition.json")
    assert state["downloads_total"] == 10
    assert len(state["attempts"][0]["events"]) == 2


def test_pinned_download_cap_retains_manifest_and_failure(tmp_path, scenario):
    config, Archive, _, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    fetch(config, data_root=data, cache_root=cache, archive=Archive())
    manifest = read_json(data, "manifest.json")
    before = (data / "manifest.json").read_bytes()
    (cache / cache_name(manifest["products"][0])).unlink()
    write_json(cache, "pinned_downloads.json", {"downloads": 21, "events": []})
    archive = Archive()
    with pytest.raises(DataValidationError, match="30"):
        fetch(config, data_root=data, cache_root=cache, archive=archive)
    assert not archive.requested
    assert (data / "manifest.json").read_bytes() == before
    assert read_json(cache, "last_fetch.json")["status"] == "failed"


@pytest.mark.parametrize(
    "versions", [("spoc-6.0.0", "spoc-5.0.0"), ("unknown", "spoc-0.0.0"), (None, None)]
)
def test_ambiguous_releases_never_rank_by_availability_or_configuration(versions):
    candidate = {
        "tic_id": "1",
        "observations": [
            observation(1, group=1, release=60000),
            observation(1, group=2, release=60001),
        ],
    }
    rows = [
        product(1, 100, group=1, version=versions[0]),
        product(1, 100, group=2, configuration="0121", version=versions[1]),
    ]
    with pytest.raises(DataValidationError, match="processing release"):
        choose_product(rows, candidate)


def test_release_ambiguity_preserved_and_does_not_skip_candidate(tmp_path, scenario):
    config, Archive, _, products, _ = scenario
    # Sanity succeeds first, then the first numeric TIC must stop, not be excluded.
    products["1001"].append(product(1, 100, configuration="0121", version="unknown"))
    archive = Archive()
    with pytest.raises(DataValidationError, match="processing release"):
        fetch(config, data_root=tmp_path / "data", cache_root=tmp_path / "cache", archive=archive)
    state = read_json(tmp_path / "data", "acquisition.json")
    assert state["attempts"][-1]["status"] == "metadata_unresolved"
    assert len(state["attempts"][-1]["product_inventory"]) == 2
    assert state["downloads_total"] == 1
    assert len(archive.requested) == 1


def test_archive_fits_version_disagreement_preserved_as_failure(tmp_path, scenario):
    config, Archive, _, products, _ = scenario
    products[str(SANITY + 1000)][0]["prvversion"] = "spoc-inconsistent"
    with pytest.raises(DataValidationError, match="processing identity"):
        fetch(config, data_root=tmp_path / "data", cache_root=tmp_path / "cache", archive=Archive())
    state = read_json(tmp_path / "data", "acquisition.json")
    assert state["attempts"][0]["status"] == "processing_unresolved"
    assert state["attempts"][0]["product"]["qc"]["processing_version"] == "spoc-5.0.0-test"
    assert state["invocations"][-1]["status"] == "failed"


def test_opaque_archive_token_requires_explicit_documented_source_release():
    product_record = {
        "archive_processing_version": "b11c787608",
        "selection_basis": "unique current archive LC identity; no release comparison",
    }
    audit = {"source_data_release": 42, "processing_version": "spoc-5.0.10-20200904"}
    result = reconcile_processing(product_record, audit)
    assert result["status"] == "archive_token_differs_source_DR42_verified"
    assert result["archive_processing_version"] == "b11c787608"
    assert result["fits_processing_version"] == "spoc-5.0.10-20200904"
    for changed in (
        {**audit, "source_data_release": 1},
        {**audit, "processing_version": "unknown"},
    ):
        with pytest.raises(DataValidationError, match="processing identity"):
            reconcile_processing(product_record, changed)


def test_qc_recheck_preserves_failure_and_reuses_exact_source(tmp_path, scenario, monkeypatch):
    config, Archive, _, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"

    def old_parser(*args, **kwargs):
        raise DataValidationError("FITS units: controlled old-parser rejection")

    with monkeypatch.context() as repair:
        repair.setattr("transitstress.acquisition.prepare_tess", old_parser)
        with pytest.raises(DataValidationError, match="FITS units"):
            fetch(config, data_root=data, cache_root=cache, archive=Archive())
    state = read_json(data, "acquisition.json")
    original_product = copy.deepcopy(state["attempts"][0]["product"])
    archive = Archive()
    fetch(config, data_root=data, cache_root=cache, archive=archive, recheck_qc=True)
    state = read_json(data, "acquisition.json")
    assert state["attempts"][0]["qc_history"][0]["status"] == "basic_qc_excluded"
    assert state["attempts"][0]["qc_history"][0]["reasons"] == [
        "FITS units: controlled old-parser rejection"
    ]
    assert state["attempts"][0]["product"]["source_sha256"] == original_product["source_sha256"]
    assert state["attempts"][0]["status"] == "basic_qc_pass"
    assert all(str(SANITY) not in uri.split("-")[2].lstrip("0") for uri in archive.requested)
    with pytest.raises(DataValidationError, match="manifest already exists"):
        fetch(config, data_root=data, cache_root=cache, archive=Archive(), recheck_qc=True)


def test_partitioned_inventory_covers_short_and_zero_padded_tics_without_paging():
    # More than one page under prefix 1, including exact TIC "1" and "01".
    rows = [observation(i, group=i) for i in range(1000, 2002)]
    rows += [
        observation(1, group=1),
        {**observation(1, group=2), "target_name": "01"},
        observation(SANITY, group=SANITY),
    ]

    class Archive:
        def query(self, service, params, *, pagesize=1000):
            assert service == "Mast.Caom.Filtered"
            selected = rows
            for f in params["filters"]:
                if f["paramName"] == "target_name":
                    if f.get("freeText"):
                        selected = [
                            r for r in selected if r["target_name"].startswith(f["freeText"][:-1])
                        ]
                    else:
                        selected = [r for r in selected if r["target_name"] in f["values"]]
            if params["columns"] == "COUNT_BIG(*)":
                assert pagesize == 1
                return [{"Column1": len(selected)}]
            assert len(selected) <= 1000  # Never asks the unstable service for page 2.
            return copy.deepcopy(selected)

    result = acquire_inventory(Archive(), str(SANITY))
    flat = [o for c in result["candidates"] for o in c["observations"]]
    assert {o["obsid"] for o in flat} == {o["obsid"] for o in rows}
    assert len(flat) == len(rows) == result["partition_verification"]["global_count"]
    assert result["partition_verification"]["data_pages"] <= 40
    assert result["partition_verification"]["count_requests"] <= 128


def test_inventory_rebuild_preserves_old_attempts_and_never_resets_cap(tmp_path, scenario):
    config, Archive, rows, _, _ = scenario
    data, cache = tmp_path / "data", tmp_path / "cache"
    # Create a controlled interrupted source, then a provably partial cached inventory.
    fetch(config, data_root=data, cache_root=cache, archive=Archive())
    state = read_json(data, "acquisition.json")
    manifest = data / "manifest.json"
    manifest.unlink()  # Test-only interrupted-before-manifest fixture, no real artifact.
    inventory = read_json(data, "candidate_inventory.json")
    inventory["observations_returned"] += 1
    write_json(data, "candidate_inventory.json", inventory)
    from transitstress.provenance import canonical_hash

    state["inventory_hash"] = canonical_hash(inventory)
    write_json(data, "acquisition.json", state)
    with pytest.raises(DataValidationError, match="paging failure"):
        fetch(config, data_root=data, cache_root=cache, archive=Archive())
    archive = Archive()
    fetch(config, data_root=data, cache_root=cache, archive=archive, rebuild_inventory=True)
    result = read_json(data, "acquisition.json")
    assert result["rejected_inventories"][0]["attempts"] == state["attempts"]
    assert result["downloads_total"] == state["downloads_total"]
    assert archive.requested == []
    assert read_json(data, "candidate_inventory_rejected_paging.json") == inventory


def test_partition_count_mismatch_stops_before_data_or_download():
    class Archive:
        def query(self, service, params, *, pagesize=1000):
            assert params["columns"] == "COUNT_BIG(*)"
            filters = [f for f in params["filters"] if f["paramName"] == "target_name"]
            return [{"Column1": 1001 if not filters or filters[0].get("freeText") == "1%" else 0}]

    with pytest.raises(DataValidationError, match="partition counts"):
        acquire_inventory(Archive(), str(SANITY))


def test_duplicate_inventory_rows_rejected_before_selection():
    class Archive:
        def query(self, service, params, *, pagesize=1000):
            return [{"Column1": 2}] if pagesize == 1 else [observation(SANITY)] * 2

    with pytest.raises(DataValidationError, match="duplicate obsid"):
        acquire_inventory(Archive(), str(SANITY))


def test_partition_count_budget_is_enforced_without_partial_result():
    class Archive:
        calls = 0

        def query(self, service, params, *, pagesize=1000):
            self.calls += 1
            assert pagesize == 1
            filters = [f for f in params["filters"] if f["paramName"] == "target_name"]
            text = filters[0].get("freeText", "") if filters else "%"
            return [{"Column1": 1001 if text and set(text[:-1]) <= {"9"} else 0}]

    archive = Archive()
    with pytest.raises(DataValidationError, match="count budget"):
        acquire_inventory(archive, str(SANITY))
    assert archive.calls == 128


def test_metadata_only_stops_before_products_and_then_reuses_inventory(tmp_path, scenario):
    config, Archive, _, _, _ = scenario
    archive = Archive()
    report = fetch(
        config,
        data_root=tmp_path / "data",
        cache_root=tmp_path / "cache",
        archive=archive,
        metadata_only=True,
    )
    assert report["status"] == "metadata_verified" and archive.requested == []
    assert all(service == "Mast.Caom.Filtered" for service, params in archive.queries)
    assert read_json(tmp_path / "data", "acquisition.json")["attempts"] == []
    resumed = Archive()
    fetch(config, data_root=tmp_path / "data", cache_root=tmp_path / "cache", archive=resumed)
    assert all(service == "Mast.Caom.Products" for service, params in resumed.queries)
