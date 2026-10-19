"""Exact cohort with real identity constants and simulated FITS/network responses."""

import copy
import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from transitstress import acquisition as acq
from transitstress import cohort
from transitstress.archive import MastArchive
from transitstress.config import load_config
from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash

EXPECTED = [
    ("261136679", "60835895"),
    ("8195886", "60844685"),
    ("8196058", "60840691"),
    ("8196285", "60835673"),
    ("8196324", "60841132"),
    ("8196402", "60832002"),
    ("24195569", "60831037"),
    ("24231627", "60841144"),
    ("24265684", "60840066"),
    ("24265755", "60830304"),
]


@pytest.fixture
def setup_cohort(tess_factory):
    config = load_config(Path("configs/protocol_v1.toml"))
    record = cohort.load_cohort(config)
    sources, observations, products = {}, {}, {}
    for pin in record["products"]:
        tic = pin["tic_id"]
        with fits.open(io.BytesIO(tess_factory(tic=int(tic), n=18001))) as hdus:
            hdus[0].header["PROCVER"] = "spoc-5.0.10-20200904"
            hdus[0].header["DATA_REL"] = 42
            stream = io.BytesIO()
            hdus.writeto(stream)
            padding = pin["declared_bytes"] - len(stream.getvalue()) - 2880
            hdus.append(fits.ImageHDU(data=np.zeros(padding, dtype=np.uint8)))
            stream = io.BytesIO()
            hdus.writeto(stream)
            sources[tic] = stream.getvalue()
        assert len(sources[tic]) == pin["declared_bytes"]
        observations[tic] = [
            {
                "obsid": int(pin["observation_group_id"]),
                "obs_id": pin["observation_id"],
                "target_name": tic,
                "obs_collection": "TESS",
                "provenance_name": "SPOC",
                "sequence_number": 1,
                "t_exptime": 120,
                "dataproduct_type": "timeseries",
                "dataRights": "PUBLIC",
                "t_obs_release": pin["archive_release_mjd"],
            }
        ]
        products[pin["observation_group_id"]] = [
            {
                "obsID": int(pin["product_group_id"]),
                "parent_obsid": int(pin["observation_group_id"]),
                "obs_id": pin["observation_id"],
                "obs_collection": "TESS",
                "dataRights": "PUBLIC",
                "productType": "SCIENCE",
                "productFilename": pin["filename"],
                "dataURI": pin["data_uri"],
                "size": pin["declared_bytes"],
                "prvversion": pin["archive_processing_version"],
            }
        ]

    class Archive(MastArchive):
        def __init__(self, offline=False):
            super().__init__(offline=offline)
            self.queries, self.downloads = [], []

        def query(self, service, params, *, pagesize=1000):
            assert not self.offline
            self.queries.append((service, copy.deepcopy(params)))
            if service == "Mast.Caom.Filtered":
                exact = [f for f in params["filters"] if f["paramName"] == "target_name"]
                assert len(exact) == 1 and "freeText" not in exact[0]
                return copy.deepcopy(observations[exact[0]["values"][0]])
            assert service == "Mast.Caom.Products"
            return copy.deepcopy(products[params["obsid"]])

        def download(self, uri, size):
            assert not self.offline
            self.budget.begin_download(size)
            self.downloads.append(uri)
            content = sources[str(int(uri.split("-")[2]))]
            self.budget.add(len(content))
            return content

    return config, record, Archive, sources, observations, products


def run(tmp_path, setup, archive=None, **kwargs):
    config, _, Archive, *_ = setup
    archive = archive if archive is not None else Archive()
    result = acq.fetch(
        config,
        data_root=tmp_path / "data",
        cache_root=tmp_path / "cache",
        archive=archive,
        **kwargs,
    )
    return result, archive


def test_approved_order_identity_and_tampering(tmp_path, monkeypatch):
    config = load_config(Path("configs/protocol_v1.toml"))
    record = cohort.load_cohort(config)
    assert [(p["tic_id"], p["observation_group_id"]) for p in record["products"]] == EXPECTED
    assert (
        canonical_hash(record) == "fdc28a9dc8e9652624ce1d09395bbe0392dd4b5c4d4aaf8ce99a9e503a240199"
    )
    for tic, pin in zip(EXPECTED, record["products"], strict=True):
        name = f"tess2018206045859-s0001-{int(tic[0]):016d}-0120-s_lc.fits"
        assert pin["filename"] == name and pin["data_uri"] == "mast:TESS/product/" + name
    path = tmp_path / "schedule.json"
    monkeypatch.setattr(cohort, "SCHEDULE", path)
    for mutation in ("order", "role", "tic_id", "data_uri"):
        bad = copy.deepcopy(record)
        bad["products"][1][mutation] = "changed"
        acq.write_json(tmp_path, path.name, bad)
        with pytest.raises(DataValidationError, match="cohort identity"):
            cohort.load_cohort(config)


def test_metadata_then_selection_and_offline_pinned_repeat(tmp_path, setup_cohort):
    result, archive = run(tmp_path, setup_cohort, metadata_only=True)
    assert result["status"] == "cohort_metadata_verified" and not archive.downloads
    assert len(archive.queries) == 20
    result, archive = run(tmp_path, setup_cohort)
    assert result["status"] == "provisional" and result["downloads"] == 8
    assert not archive.queries
    manifest_path = tmp_path / "data/manifest.json"
    before = manifest_path.read_bytes()
    manifest = acq.read_json(tmp_path / "data", "manifest.json")
    assert [p["tic_id"] for p in manifest["products"]] == [t for t, _ in EXPECTED[:8]]
    assert [p["role"] for p in manifest["products"]] == ["sanity", "development", "development"] + [
        "evaluation"
    ] * 5
    with pytest.raises(DataValidationError, match="T03"):
        acq.require_final_manifest(manifest)
    _, archive = run(tmp_path, setup_cohort, setup_cohort[2](offline=True))
    assert not archive.queries and not archive.downloads and manifest_path.read_bytes() == before
    acq.write_selection_csv(tmp_path / "data")
    assert "basic_qc_pass" in (tmp_path / "data/selection.csv").read_text()


def test_unrelated_products_cannot_change_pins(tmp_path, setup_cohort):
    products = setup_cohort[-1]
    for rows in products.values():
        unrelated = {
            **rows[0],
            "dataURI": rows[0]["dataURI"].replace("0120", "0121"),
            "productFilename": rows[0]["productFilename"].replace("0120", "0121"),
        }
        rows.insert(0, unrelated)
    _, archive = run(tmp_path, setup_cohort)
    assert archive.downloads == [p["data_uri"] for p in setup_cohort[1]["products"][:8]]


@pytest.mark.parametrize(
    "issue",
    ["missing", "different_uri", "changed_version", "conflicting_duplicate", "wrong_observation"],
)
def test_metadata_disagreements_stop_without_exclusions_or_downloads(tmp_path, setup_cohort, issue):
    pin = setup_cohort[1]["products"][1]
    products = setup_cohort[-1]
    rows = products[pin["observation_group_id"]]
    if issue == "missing":
        rows.clear()
    elif issue == "different_uri":
        rows[0]["dataURI"] = rows[0]["dataURI"].replace("0120", "0121")
    elif issue == "changed_version":
        rows[0]["prvversion"] = "unknown"
    elif issue == "conflicting_duplicate":
        rows.append({**rows[0], "size": rows[0]["size"] + 1})
    else:
        setup_cohort[-2][pin["tic_id"]][0]["obs_id"] = "changed"
    archive = setup_cohort[2]()
    with pytest.raises(DataValidationError):
        run(tmp_path, setup_cohort, archive)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert state["status"] == "incomplete" and state["downloads_total"] == 0
    assert not archive.downloads and not state["attempts"]
    assert state["metadata_attempts"][-1]["status"] == "failed"
    assert state["metadata_attempts"][-1]["evidence"]


def test_transport_failure_retries_same_pin_and_retains_reservation(tmp_path, setup_cohort):
    archive = setup_cohort[2]()

    def fail(uri, size):
        archive.budget.begin_download(size)
        raise DataValidationError("mock transport unavailable")

    archive.download = fail
    with pytest.raises(DataValidationError, match="transport"):
        run(tmp_path, setup_cohort, archive)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert state["downloads_total"] == 1 and len(state["attempts"]) == 1
    assert state["attempts"][0]["status"] == "selected"
    _, resumed = run(tmp_path, setup_cohort)
    assert (
        not resumed.queries and resumed.downloads[0] == setup_cohort[1]["products"][0]["data_uri"]
    )
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert state["downloads_total"] == 9 and len(state["attempts"][0]["events"]) == 2


def test_reserves_only_for_uniform_qc_and_finite_pool_exhaustion(
    tmp_path, setup_cohort, monkeypatch
):
    original = acq.basic_qc_reasons
    rejected = {"8195886", "8196058"}

    def reasons(curve, audit, config):
        if any(curve.curve_id.startswith(f"tic{t}-") for t in rejected):
            return ["controlled technical exclusion"]
        return original(curve, audit, config)

    monkeypatch.setattr(acq, "basic_qc_reasons", reasons)
    run(tmp_path, setup_cohort)
    manifest = acq.read_json(tmp_path / "data", "manifest.json")
    assert [p["tic_id"] for p in manifest["products"]][1:] == [t for t, _ in EXPECTED[3:]]
    assert manifest["downloads_total"] == 10
    # Independent second fixture directory; third exclusion makes seven impossible.
    rejected.add("8196285")
    with pytest.raises(DataValidationError, match="finite-pool exhaustion"):
        run(tmp_path / "exhausted", setup_cohort)
    state = acq.read_json(tmp_path / "exhausted/data", cohort.JOURNAL)
    assert len(state["attempts"]) == 10 and state["downloads_total"] == 10


@pytest.mark.parametrize(
    "boundary",
    [
        "journal_initial",
        "reservation",
        "source_identity",
        "cache_install",
        "manifest_ready",
        "manifest_installed",
    ],
)
def test_interruption_resume_keeps_pins_and_manifest_identity(
    tmp_path, setup_cohort, monkeypatch, boundary
):
    write, install = acq.write_json, acq.atomic_write
    fired = False

    def interrupted(root, name, value):
        nonlocal fired
        write(root, name, value)
        trigger = (
            (boundary == "journal_initial" and name == cohort.JOURNAL and not value["invocations"])
            or (
                boundary == "reservation"
                and name == cohort.JOURNAL
                and value["downloads_total"] == 1
            )
            or (
                boundary == "source_identity"
                and name == cohort.JOURNAL
                and value["attempts"]
                and "source_sha256" in value["attempts"][0]["product"]
            )
            or (
                boundary == "manifest_ready"
                and name == cohort.JOURNAL
                and value["status"] == "manifest_ready"
            )
            or (boundary == "manifest_installed" and name == "manifest.json")
        )
        if trigger and not fired:
            fired = True
            raise KeyboardInterrupt("controlled interruption")

    def interrupted_cache(root, name, content):
        nonlocal fired
        install(root, name, content)
        if boundary == "cache_install" and name.endswith(".fits") and not fired:
            fired = True
            raise KeyboardInterrupt("controlled cache interruption")

    with monkeypatch.context() as patch:
        patch.setattr(acq, "write_json", interrupted)
        patch.setattr(acq, "atomic_write", interrupted_cache)
        with pytest.raises(KeyboardInterrupt):
            run(tmp_path, setup_cohort)
    assert fired
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    pending = state.get("pending_manifest")
    run(tmp_path, setup_cohort)
    manifest = acq.read_json(tmp_path / "data", "manifest.json")
    assert [p["tic_id"] for p in manifest["products"]] == [t for t, _ in EXPECTED[:8]]
    if pending:
        assert manifest == pending
    assert acq.read_json(tmp_path / "data", cohort.JOURNAL)["status"] == "provisional"
    assert manifest["downloads_total"] == (
        9 if boundary in ("reservation", "source_identity") else 8
    )


def test_actual_legacy_snapshot_migration_repeat_and_conflict(tmp_path):
    config = load_config(Path("configs/protocol_v1.toml"))
    record = cohort.load_cohort(config)
    data = tmp_path / "data"
    data.mkdir()
    originals = {}
    for name in cohort.LEGACY_HASHES:
        originals[name] = (Path("data") / name).read_bytes()
        (data / name).write_bytes(originals[name])
    first = cohort.initialize(config, data, record)
    second = cohort.initialize(config, data, record)
    assert first == second and first["downloads_total"] == 5
    assert first["history"] == acq.read_json(Path("data"), "acquisition.json")
    assert [a["tic_id"] for a in first["attempts"]] == [
        "261136679",
        "8195886",
        "8196285",
        "8196324",
    ]
    assert all((data / n).read_bytes() == b for n, b in originals.items())
    state = copy.deepcopy(first)
    state["attempts"][0]["product"]["data_uri"] = state["attempts"][0]["product"][
        "data_uri"
    ].replace("0120", "0121")
    acq.write_json(data, cohort.JOURNAL, state)
    with pytest.raises(DataValidationError, match="pin"):
        cohort.initialize(config, data, record)
    acq.write_json(data, cohort.JOURNAL, first)
    state = copy.deepcopy(first)
    state["downloads_total"] = 4
    acq.write_json(data, cohort.JOURNAL, state)
    with pytest.raises(DataValidationError, match="accounting"):
        cohort.initialize(config, data, record)
    (data / "candidate_inventory_rejected_paging.json").write_bytes(b"{}\n")
    with pytest.raises(DataValidationError, match="historical evidence"):
        cohort.initialize(config, data, record)


def test_migrated_valid_cache_hits_keep_five_reservations(tmp_path, setup_cohort, monkeypatch):
    config, record, Archive, sources, *_ = setup_cohort
    cache = tmp_path / "cache"
    old_attempts = []
    for pin in record["products"]:
        if pin["tic_id"] not in {"261136679", "8195886", "8196285", "8196324"}:
            continue
        content = sources[pin["tic_id"]]
        curve, audit = acq.prepare_tess(content, tic_id=pin["tic_id"], config=config)
        product = {k: pin[k] for k in cohort.PIN_KEYS}
        product.update(
            source_sha256=acq.source_sha256(content),
            byte_size=len(content),
            acquired_utc="2026-10-19T17:00:00+00:00",
            qc=audit,
            selection_basis="unique current archive LC identity; no release comparison",
        )
        product["processing_reconciliation"] = acq.reconcile_processing(product, audit)
        acq.atomic_write(cache, acq.cache_name(product), content)
        old_attempts.append(
            {"tic_id": pin["tic_id"], "status": "basic_qc_pass", "product": product, "events": []}
        )
    history = {
        "downloads_total": 5,
        "attempts": [],
        "invocations": [{"status": "failed"}],
        "rejected_inventories": [{"attempts": old_attempts}],
    }
    monkeypatch.setattr(cohort, "_legacy", lambda data: copy.deepcopy(history))
    result, archive = run(tmp_path, setup_cohort)
    assert result["downloads"] == 4
    manifest = acq.read_json(tmp_path / "data", "manifest.json")
    assert manifest["downloads_total"] == 9
    assert all(
        t not in {str(int(u.split("-")[2])) for u in archive.downloads}
        for t in {"261136679", "8195886", "8196285", "8196324"}
    )
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert state["history"] == history and len(state["reservations"]) == 4
    for old in old_attempts:
        current = next(p for p in manifest["products"] if p["tic_id"] == old["tic_id"])
        assert current["source_sha256"] == old["product"]["source_sha256"]
        assert current["acquired_utc"] == old["product"]["acquired_utc"]


def test_actual_rejection_migration_survives_repeated_metadata_failure(tmp_path):
    config = load_config(Path("configs/protocol_v1.toml"))
    data = tmp_path / "data"
    data.mkdir()
    for name in cohort.LEGACY_HASHES:
        (data / name).write_bytes((Path("data") / name).read_bytes())

    class Failed(MastArchive):
        def query(self, *args, **kwargs):
            raise DataValidationError("mock metadata timeout")

    for _ in range(2):
        with pytest.raises(DataValidationError, match="metadata timeout"):
            acq.fetch(
                config,
                data_root=data,
                cache_root=tmp_path / "cache",
                archive=Failed(),
                metadata_only=True,
            )
    state = acq.read_json(data, cohort.JOURNAL)
    assert state["downloads_total"] == 5 and state["reservations"] == []
    assert len(state["attempts"]) == 4 and len(state["invocations"]) == 2
    assert len(state["history"]["rejected_inventories"]) == 1
    assert all(
        (data / n).read_bytes() == (Path("data") / n).read_bytes() for n in cohort.LEGACY_HASHES
    )


def test_accounting_exhaustion_and_corrupt_cache_are_not_exclusions(tmp_path, setup_cohort):
    run(tmp_path, setup_cohort, metadata_only=True)
    data = tmp_path / "data"
    state = acq.read_json(data, cohort.JOURNAL)
    state["downloads_total"] = 30
    state["reservations"] = [{"tic_id": "261136679"}] * 30
    acq.write_json(data, cohort.JOURNAL, state)
    archive = setup_cohort[2]()
    with pytest.raises(DataValidationError, match="30 spent"):
        run(tmp_path, setup_cohort, archive)
    assert not archive.downloads
    state = acq.read_json(data, cohort.JOURNAL)
    assert state["downloads_total"] == 30 and state["attempts"][0]["status"] == "selected"
    # A separately completed mock then demonstrates corruption is preserved.
    other = tmp_path / "corrupt"
    run(other, setup_cohort)
    manifest = acq.read_json(other / "data", "manifest.json")
    cache_file = other / "cache" / acq.cache_name(manifest["products"][0])
    cache_file.write_bytes(b"corrupt")
    with pytest.raises(DataValidationError, match="corrupt"):
        run(other, setup_cohort)
    assert cache_file.read_bytes() == b"corrupt"


def test_interrupted_source_identity_rejects_changed_bytes(tmp_path, setup_cohort, monkeypatch):
    write = acq.write_json
    fired = False

    def interrupt(root, name, value):
        nonlocal fired
        write(root, name, value)
        if (
            name == cohort.JOURNAL
            and value["attempts"]
            and "source_sha256" in value["attempts"][0]["product"]
            and not fired
        ):
            fired = True
            raise KeyboardInterrupt("after hash, before cache")

    with monkeypatch.context() as patch:
        patch.setattr(acq, "write_json", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(tmp_path, setup_cohort)
    sources = setup_cohort[3]
    b = sources["261136679"]
    sources["261136679"] = b[:-1] + bytes([b[-1] ^ 1])
    with pytest.raises(DataValidationError, match="pinned hash changed"):
        run(tmp_path, setup_cohort)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert state["downloads_total"] == 2 and len(state["attempts"]) == 1
    assert state["attempts"][0]["product"]["source_sha256"] == acq.source_sha256(b)


def test_cohort_qc_recheck_preserves_history_without_downloading(
    tmp_path, setup_cohort, monkeypatch
):
    def old_parser(*args, **kwargs):
        # A scientific QC rejection still requires a complete identity audit.
        raise acq.TessQCError(
            "controlled prior QC failure",
            {
                "input_rows": 18001,
                "processing_version": "spoc-5.0.10-20200904",
                "source_data_release": 42,
            },
        )

    with monkeypatch.context() as patch:
        patch.setattr(acq, "prepare_tess", old_parser)
        with pytest.raises(DataValidationError, match="sanity QC"):
            run(tmp_path, setup_cohort)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    original = copy.deepcopy(state["attempts"][0]["product"])
    _, archive = run(tmp_path, setup_cohort, recheck_qc=True)
    assert original["data_uri"] not in archive.downloads
    after = acq.read_json(tmp_path / "data", cohort.JOURNAL)["attempts"][0]
    assert after["qc_history"][0]["reasons"] == ["controlled prior QC failure"]
    assert after["product"]["source_sha256"] == original["source_sha256"]
    assert after["product"]["acquired_utc"] == original["acquired_utc"]


def test_pending_manifest_conflict_stops_without_overwrite(tmp_path, setup_cohort, monkeypatch):
    write = acq.write_json
    fired = False

    def interrupt(root, name, value):
        nonlocal fired
        write(root, name, value)
        if name == "manifest.json" and not fired:
            fired = True
            raise KeyboardInterrupt("after installing manifest")

    with monkeypatch.context() as patch:
        patch.setattr(acq, "write_json", interrupt)
        with pytest.raises(KeyboardInterrupt):
            run(tmp_path, setup_cohort)
    manifest = acq.read_json(tmp_path / "data", "manifest.json")
    manifest["products"][0]["source_sha256"] = "0" * 64
    write(tmp_path / "data", "manifest.json", manifest)
    before = (tmp_path / "data/manifest.json").read_bytes()
    with pytest.raises(DataValidationError, match="installed identity conflict"):
        run(tmp_path, setup_cohort)
    assert (tmp_path / "data/manifest.json").read_bytes() == before


def test_source_identity_processing_and_offline_errors_do_not_use_reserve(tmp_path, setup_cohort):
    config, _, Archive, sources, *_ = setup_cohort
    run(tmp_path, setup_cohort, metadata_only=True)
    archive = Archive(offline=True)
    with pytest.raises(DataValidationError, match="offline"):
        run(tmp_path, setup_cohort, archive)
    assert acq.read_json(tmp_path / "data", cohort.JOURNAL)["downloads_total"] == 0
    original = sources["261136679"]
    with fits.open(io.BytesIO(original)) as hdus:
        hdus[0].header["PROCVER"] = "unknown"
        stream = io.BytesIO()
        hdus.writeto(stream)
        sources["261136679"] = stream.getvalue()
    with pytest.raises(DataValidationError, match="processing identity"):
        run(tmp_path, setup_cohort)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert len(state["attempts"]) == 1 and state["attempts"][0]["status"] == "processing_unresolved"
    assert state["downloads_total"] == 1
    with pytest.raises(DataValidationError, match="frozen"):
        acq.fetch(
            replace(config, frozen=True),
            data_root=tmp_path / "new",
            cache_root=tmp_path / "cache",
            archive=Archive(),
        )


def test_mock_final_exact_reacquisition_and_hash_conflict(tmp_path, setup_cohort):
    config, _, Archive, sources, *_ = setup_cohort
    run(tmp_path, setup_cohort)
    data = tmp_path / "data"
    manifest = acq.read_json(data, "manifest.json")
    manifest.update(status="final", support_validation="complete", roles_provisional=False)
    acq.write_json(data, "manifest.json", manifest)
    before = (data / "manifest.json").read_bytes()
    archive = Archive()
    acq.fetch(
        replace(config, frozen=True), data_root=data, cache_root=tmp_path / "clean", archive=archive
    )
    assert not archive.queries and len(archive.downloads) == 8
    assert (data / "manifest.json").read_bytes() == before
    p = manifest["products"][0]
    (tmp_path / "clean" / acq.cache_name(p)).unlink()
    b = sources[p["tic_id"]]
    sources[p["tic_id"]] = b[:-1] + bytes([b[-1] ^ 1])
    with pytest.raises(DataValidationError, match="hash"):
        acq.fetch(config, data_root=data, cache_root=tmp_path / "clean", archive=Archive())
    assert (data / "manifest.json").read_bytes() == before
    for flag in ("recheck_qc", "metadata_only", "rebuild_inventory"):
        with pytest.raises(DataValidationError):
            run(tmp_path, setup_cohort, **{flag: True})


def test_r1_qc_failure_cannot_hide_provenance_conflict(tmp_path, setup_cohort):
    # Review R1: zero usable rows and unknown/DR41 provenance on candidate 8195886.
    sources = setup_cohort[3]
    with fits.open(io.BytesIO(sources["8195886"])) as hdus:
        hdus[0].header["PROCVER"] = "unknown"
        hdus[0].header["DATA_REL"] = 41
        hdus[1].data["QUALITY"][:] = 17087
        stream = io.BytesIO()
        hdus.writeto(stream)
        sources["8195886"] = stream.getvalue()
    archive = setup_cohort[2]()
    with pytest.raises(DataValidationError, match="processing identity"):
        run(tmp_path, setup_cohort, archive)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    failed = state["attempts"][-1]
    assert [a["tic_id"] for a in state["attempts"]] == ["261136679", "8195886"]
    assert failed["status"] == "processing_unresolved"
    assert failed["product"]["qc"]["processing_version"] == "unknown"
    assert failed["product"]["qc"]["source_data_release"] == 41
    assert failed["product"]["qc"]["retained_rows"] == 0
    assert failed["qc_rejection_reasons"] == ["FITS retained rows: fewer than two; reject product"]
    assert state["downloads_total"] == 2 and len(state["reservations"]) == 2
    assert archive.downloads == [p["data_uri"] for p in setup_cohort[1]["products"][:2]]
    assert not (tmp_path / "data/manifest.json").exists()


def test_r1_valid_provenance_qc_rejection_can_use_reserve(tmp_path, setup_cohort):
    sources = setup_cohort[3]
    with fits.open(io.BytesIO(sources["8195886"])) as hdus:
        hdus[1].data["QUALITY"][:] = 17087
        stream = io.BytesIO()
        hdus.writeto(stream)
        sources["8195886"] = stream.getvalue()
    run(tmp_path, setup_cohort)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    excluded = next(a for a in state["attempts"] if a["tic_id"] == "8195886")
    assert excluded["status"] == "basic_qc_excluded"
    assert excluded["product"]["processing_reconciliation"]["source_data_release"] == 42
    manifest = acq.read_json(tmp_path / "data", "manifest.json")
    assert [p["tic_id"] for p in manifest["products"]] == [
        "261136679",
        "8196058",
        "8196285",
        "8196324",
        "8196402",
        "24195569",
        "24231627",
        "24265684",
    ]


def test_r1_saved_qc_exclusion_requires_provenance_on_resume(tmp_path, setup_cohort):
    run(tmp_path, setup_cohort, metadata_only=True)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    product = copy.deepcopy(state["metadata"]["8195886"]["product"])
    product["qc"] = {"processing_version": "unknown", "source_data_release": 41}
    state["attempts"] = [
        {
            "tic_id": "8195886",
            "product": product,
            "status": "basic_qc_excluded",
            "reasons": ["prior QC failure"],
            "events": [],
        }
    ]
    acq.write_json(tmp_path / "data", cohort.JOURNAL, state)
    archive = setup_cohort[2]()
    with pytest.raises(DataValidationError, match="processing identity"):
        run(tmp_path, setup_cohort, archive)
    assert archive.downloads == [setup_cohort[1]["products"][0]["data_uri"]]
    assert not (tmp_path / "data/manifest.json").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", 0),
        ("dataRights", "PRIVATE"),
        ("productType", "INFO"),
        ("obs_collection", "OTHER"),
        ("parent_obsid", 0),
        ("obsID", 0),
        ("obs_id", "wrong-observation"),
        ("productFilename", "wrong_lc.fits"),
        ("prvversion", 42),
    ],
)
def test_r2_ineligible_same_uri_row_is_identity_conflict_before_downloads(
    tmp_path, setup_cohort, field, value
):
    pin = setup_cohort[1]["products"][1]
    rows = setup_cohort[-1][pin["observation_group_id"]]
    rows.append({**rows[0], field: value})
    archive = setup_cohort[2]()
    with pytest.raises(DataValidationError, match="identity.*conflict"):
        run(tmp_path, setup_cohort, archive, metadata_only=True)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    assert state["downloads_total"] == 0 and not archive.downloads and not state["attempts"]
    assert state["metadata_attempts"][-1]["status"] == "failed"
    assert state["metadata_attempts"][-1]["evidence"]["products"] == rows
    assert not (tmp_path / "data/manifest.json").exists()


def test_r2_matching_duplicates_are_safe_but_historical_raw_conflict_stops(
    tmp_path, setup_cohort, monkeypatch
):
    config, record, Archive, _, _, products = setup_cohort
    pin = record["products"][1]
    valid = products[pin["observation_group_id"]][0]
    products[pin["observation_group_id"]].append(copy.deepcopy(valid))
    assert cohort.verify_metadata(pin, Archive(), {})["product"]["declared_bytes"] == 2039040
    # Prior selecting attempt has no chosen product: its contradictory rows must
    # still be inspected rather than silently skipped during migration.
    history = {
        "downloads_total": 5,
        "attempts": [],
        "invocations": [],
        "rejected_inventories": [
            {
                "attempts": [
                    {
                        "tic_id": pin["tic_id"],
                        "status": "selecting",
                        "product_inventory": [valid, {**valid, "size": 0}],
                    }
                ]
            }
        ],
    }
    before = copy.deepcopy(history)
    monkeypatch.setattr(cohort, "_legacy", lambda root: history)
    with pytest.raises(DataValidationError, match="identity.*conflict"):
        cohort.initialize(config, tmp_path / "data", record)
    assert history == before and not (tmp_path / "data" / cohort.JOURNAL).exists()


def test_r2_saved_metadata_conflict_is_not_trusted_on_resume(tmp_path, setup_cohort):
    run(tmp_path, setup_cohort, metadata_only=True)
    state = acq.read_json(tmp_path / "data", cohort.JOURNAL)
    verified = state["metadata"]["8195886"]
    rows = verified["evidence"]["products"]
    rows.append({**rows[0], "size": 0})
    verified["evidence_hash"] = canonical_hash(verified["evidence"])
    acq.write_json(tmp_path / "data", cohort.JOURNAL, state)
    before = (tmp_path / "data" / cohort.JOURNAL).read_bytes()
    archive = setup_cohort[2]()
    with pytest.raises(DataValidationError, match="identity.*conflict"):
        run(tmp_path, setup_cohort, archive)
    assert not archive.queries and not archive.downloads
    assert (tmp_path / "data" / cohort.JOURNAL).read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "b" * 64),
        ("declared_bytes", 0),
        ("archive_processing_version", "conflicting-version"),
    ],
)
def test_r2_conflicting_historical_pins_are_not_silently_chosen(
    tmp_path, setup_cohort, monkeypatch, field, value
):
    config, record, *_ = setup_cohort
    pin = record["products"][1]
    product = {k: pin[k] for k in cohort.PIN_KEYS}
    product.update(source_sha256="a" * 64, byte_size=2039040)
    incomplete = {k: pin[k] for k in cohort.PIN_KEYS}
    conflicting = {**product, field: value}
    history = {
        "downloads_total": 5,
        "invocations": [],
        "rejected_inventories": [],
        "attempts": [
            {"tic_id": pin["tic_id"], "product": p} for p in (product, incomplete, conflicting)
        ],
    }
    before = copy.deepcopy(history)
    monkeypatch.setattr(cohort, "_legacy", lambda root: history)
    with pytest.raises(DataValidationError, match="identity.*conflict"):
        cohort.initialize(config, tmp_path / "data", record)
    assert history == before and not (tmp_path / "data" / cohort.JOURNAL).exists()
