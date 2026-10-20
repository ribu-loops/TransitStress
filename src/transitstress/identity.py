"""Scientific content identity, separate from execution and Git provenance."""

import hashlib
import importlib.metadata
import os
import platform
import shutil

# Only fixed Git argument lists are executed below, without a shell.
import subprocess  # nosec B404
from pathlib import Path

from transitstress.acquisition import utc_now
from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scientific_inventory(root: Path) -> dict[str, str]:
    """Discover additions/deletions; exclude only explicit presentation directories.

    All package Python and all scientific scripts participate, including CLI and
    serialization. check.py is verification tooling, not a scientific executor.
    No newly imported package helper can silently escape this inventory.
    """
    paths = set()
    for folder in ("src", "scripts"):
        for path in (root / folder).rglob("*.py"):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or "ui" in relative.parts:
                continue
            if relative.as_posix() == "scripts/check.py":
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise DataValidationError("scientific source: unsafe link; restore regular files")
            paths.add(path)
    for name in ("pyproject.toml", ".python-version"):
        paths.add(root / name)
    for pattern in ("*.toml", "*.json"):
        paths.update((root / "configs").rglob(pattern))
    return {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(paths)}


def run_identity(root: Path, *, settings: object, data: object, split: str) -> dict:
    inventory = scientific_inventory(root)
    return dict(
        schema_version=1,
        settings=settings,
        data=data,
        split=split,
        scientific_files=inventory,
        scientific_source_hash=canonical_hash(inventory),
        lock_hash=file_hash(root / "uv.lock"),
        python=platform.python_version(),
        dependency_versions={
            d.metadata["Name"].lower(): d.version for d in importlib.metadata.distributions()
        },
    )


def execution_provenance(root: Path) -> dict:
    # Fixed commands only; no input text is ever used as a command or shell string.
    git = shutil.which("git")
    if git is None:
        raise DataValidationError("execution provenance: Git unavailable; restore Git on PATH")
    commit = subprocess.run(  # nosec B603
        [git, "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    dirty = subprocess.run(  # nosec B603
        [git, "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return dict(
        execution_commit=commit,
        recorded_utc=utc_now(),
        dirty=bool(dirty),
        python=platform.python_version(),
        os=platform.platform(),
        machine=platform.machine(),
        logical_cpus=os.cpu_count(),
        hardware=hardware_facts(),
    )


def hardware_facts() -> dict:
    """Named public machine facts only; no environment or system inventory dump."""
    result = dict(cpu_name=None, physical_memory_bytes=None, reason="platform_api_unavailable")
    if os.name == "nt":
        import ctypes
        import winreg

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total_phys",
                    "avail_phys",
                    "total_page",
                    "avail_page",
                    "total_virtual",
                    "avail_virtual",
                    "extended_virtual",
                )
            ]

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                result["cpu_name"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            api = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
            api.argtypes = [ctypes.POINTER(MemoryStatus)]
            api.restype = ctypes.c_int
            if api(ctypes.byref(status)):
                result["physical_memory_bytes"] = status.total_phys
            result["reason"] = None if result["physical_memory_bytes"] else "memory_api_unavailable"
        except OSError:
            result["reason"] = "hardware_api_unavailable"
    return result
