"""Session-owned dashboard adapter to the frozen, headless T06 interface."""

import hashlib
import threading
from _thread import LockType
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from transitstress.config import app_mode, load_config
from transitstress.csv_input import CSVInput, parse_csv
from transitstress.errors import DataValidationError
from transitstress.experiment import load_split
from transitstress.identity import file_hash, scientific_inventory
from transitstress.models import PreparedCurve
from transitstress.provenance import canonical_hash
from transitstress.workbench import local_spec, single_case

ROOT = Path(__file__).resolve().parents[3]
BUILT_INS = ("8195886", "8196058")


def require_local() -> None:
    """Read the trusted process setting each time; no mode from query/session data."""
    if app_mode() != "local":
        raise DataValidationError(
            "Workbench mode: demo denies uploads and new computation; launch local "
            "mode on 127.0.0.1"
        )


def accept_csv(content: bytes, confirmed: bool) -> CSVInput:
    require_local()
    return parse_csv(
        content,
        confirm_days_and_linear_units=confirmed,
        config=load_config(ROOT / "configs/protocol_v1.toml"),
    )


def built_in_curve(tic: str) -> PreparedCurve:
    require_local()
    if tic not in BUILT_INS:
        raise DataValidationError("built-in: unsupported; select a development TIC")
    config = load_config(ROOT / "configs/protocol_v1.toml")
    _, curves = load_split(
        config, ROOT / "data/manifest.json", ROOT / ".cache/transitstress", "development"
    )
    return curves[tic]


@dataclass(frozen=True)
class Request:
    source: str
    period_days: float = 3.41
    depth_ppm: float = 750.0
    duration_hours: float = 3.0
    epoch_fraction: float = 0.43
    mask_name: str = "original"
    confirmed: bool = False
    csv_content: bytes | None = field(default=None, repr=False)

    def arguments(self) -> dict:
        return dict(
            period_days=self.period_days,
            depth_ppm=self.depth_ppm,
            duration_hours=self.duration_hours,
            epoch_fraction=self.epoch_fraction,
            mask_name=self.mask_name,
        )

    def fingerprint(self) -> str:
        config = load_config(ROOT / "configs/protocol_v1.toml")
        return canonical_hash(
            dict(
                source=self.source,
                settings=self.arguments(),
                confirmed=self.confirmed,
                content_sha256=hashlib.sha256(self.csv_content).hexdigest()
                if self.csv_content
                else None,
                protocol=config,
                manifest=file_hash(ROOT / "data/manifest.json") if self.source != "CSV" else None,
                scientific_files=scientific_inventory(ROOT),
                lock=file_hash(ROOT / "uv.lock"),
            )
        )


def prepare(request: Request) -> tuple[PreparedCurve, tuple[str, ...]]:
    require_local()
    if request.confirmed is not True:
        raise DataValidationError(
            "input units: unconfirmed; confirm days and linear flux/error units"
        )
    if request.source == "CSV":
        parsed = accept_csv(request.csv_content, request.confirmed)
        curve, notices = parsed.curve, parsed.notices
    else:
        if request.csv_content is not None:
            raise DataValidationError("input: ambiguous; choose only built-in or CSV")
        curve, notices = built_in_curve(request.source), ()
    config = load_config(ROOT / "configs/protocol_v1.toml")
    if request.mask_name not in config.masks.names:
        raise DataValidationError("mask_name: unsupported; select an available observation mask")
    local_spec(curve, **{k: v for k, v in request.arguments().items() if k != "mask_name"})
    return curve, notices


@dataclass
class SessionJob:
    """Create one instance per Streamlit session. No shared/persistent caches."""

    request: Request | None = None
    fingerprint: str | None = None
    result: dict | None = None
    elapsed_seconds: float | None = None
    evidence: bytes | None = field(default=None, repr=False)
    lock: LockType = field(default_factory=threading.Lock, repr=False)

    @property
    def running(self) -> bool:
        return self.lock.locked()

    def run(self, request: Request) -> bool:
        require_local()
        if not self.lock.acquire(blocking=False):
            raise DataValidationError("run: already running; wait for the current comparison")
        try:
            curve, _ = prepare(request)
            fingerprint = request.fingerprint()
            if self.result is not None and self.fingerprint == fingerprint:
                return False
            started = perf_counter()
            config = load_config(ROOT / "configs/protocol_v1.toml")
            result = single_case(
                config,
                repository=ROOT,
                **request.arguments(),
                built_in=None if request.source == "CSV" else request.source,
                csv_content=request.csv_content if request.source == "CSV" else None,
                confirm_days_and_linear_units=request.confirmed,
                manifest_path=ROOT / "data/manifest.json",
                cache=ROOT / ".cache/transitstress",
            )
            expected_spec = local_spec(
                curve, **{k: v for k, v in request.arguments().items() if k != "mask_name"}
            )
            identity = result["identity"]
            if (
                result["run_id"] != canonical_hash(identity)
                or canonical_hash(identity["settings"]["spec"]) != canonical_hash(expected_spec)
                or identity["data"]["prepared_hash"] != curve.content_hash
                or any(r["mask_name"] != request.mask_name for r in result["rows"])
            ):
                raise DataValidationError(
                    "result identity: differs from executed input/settings; preserve and diagnose"
                )
            self.elapsed_seconds = perf_counter() - started
            # Publish atomically after completion. Failed rows remain genuine evidence.
            self.request, self.fingerprint, self.result = request, fingerprint, result
            self.evidence = None
            return True
        finally:
            self.lock.release()

    def export(self) -> bytes:
        require_local()
        if not self.lock.acquire(blocking=False):
            raise DataValidationError("evidence: job busy; wait for the current comparison/export")
        try:
            if self.result is None:
                raise DataValidationError("evidence: no completed result; run a comparison first")
            if self.evidence is None:
                from transitstress.ui.evidence import evidence_zip

                self.evidence = evidence_zip(self.result, self.request, self.fingerprint)
            return self.evidence
        finally:
            self.lock.release()
