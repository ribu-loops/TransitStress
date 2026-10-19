"""Offline fixture products exercise the real selection/cache/QC orchestration."""

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from transitstress.acquisition import (
    cache_name,
    candidate_inventory,
    choose_product,
    fetch,
    read_json,
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


def product(tic, size, group=None, configuration="0120", version="spoc-5.0.0"):
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
def scenario(tess_factory):
    config = load_config(Path("configs/protocol_v1.toml"))
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

        def query(self, service, params):
            assert not self.offline
            self.queries.append((service, params))
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
    inventory = candidate_inventory(observations + [observations[0]], config.sanity.tic_id)
    assert [c["tic_id"] for c in inventory["candidates"]][:3] == ["1", "2", "3"]
    candidate = next(c for c in inventory["candidates"] if c["tic_id"] == "2")
    candidate["observations"].append(observation(2, group=99, release=60001.0))
    rows = products["1002"] + [product(2, 100, group=99, configuration="0121")]
    wrong = {**rows[0], "productFilename": "target_tp.fits"}
    chosen, evidence = choose_product(rows + [wrong], candidate)
    assert chosen["observation_group_id"] == "99"
    assert evidence[-1]["exclusion_reason"]
    tied = [product(2, 100, group=99, configuration=v) for v in ("0122", "0121")]
    chosen, _ = choose_product(tied, candidate)
    assert chosen["filename"].endswith("0121-s_lc.fits")
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
