"""Fixed, bounded verification commands. Missing required suites are incomplete (exit 2)."""

import argparse
import hashlib
import json
import platform
import shutil

# The development wrapper requires subprocess; owned fixed argument lists are tested.
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(arguments: list[str], timeout: int = 300, quiet: bool = False) -> int:
    """Use only developer-owned argument lists; never invoke a shell."""
    try:
        # Callers below supply only fixed tools/flags and generated paths, never external data.
        result = subprocess.run(  # nosec B603
            arguments,
            cwd=ROOT,
            timeout=timeout,
            check=False,
            shell=False,
            capture_output=quiet,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("INCOMPLETE: required command unavailable or timed out", flush=True)
        return 2
    if result.returncode:
        print(f"FAILED: required command exited {result.returncode}", flush=True)
    return result.returncode


def suite(directory: str) -> int:
    if not any((ROOT / directory).glob("test_*.py")):
        print(f"INCOMPLETE: {directory} not implemented", flush=True)
        return 2
    return run([PYTHON, "-m", "pytest", directory])


def scan_secrets() -> int:
    """Stage only tracked/nonignored regular project files, with containment checks."""
    pin_path = ROOT / "scripts/gitleaks-pin.json"
    if not pin_path.is_file():
        print("INCOMPLETE: install pinned Gitleaks as documented in docs/ENVIRONMENT.md")
        return 2
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    machine = platform.machine().lower()
    architecture = "x86_64" if machine in {"amd64", "x86_64"} else machine
    selected = pin["platforms"].get(f"{platform.system()}-{architecture}")
    if selected is None:
        print("INCOMPLETE: no reviewed Gitleaks pin for this platform")
        return 2
    executable = ROOT / ".tools" / selected["filename"]
    if not executable.is_file():
        print("INCOMPLETE: install pinned Gitleaks as documented in docs/ENVIRONMENT.md")
        return 2
    if hashlib.sha256(executable.read_bytes()).hexdigest() != selected["executable_sha256"]:
        print("INCOMPLETE: Gitleaks executable checksum mismatch")
        return 2
    git = shutil.which("git")
    if git is None:
        print("INCOMPLETE: Git unavailable")
        return 2
    listing = (
        subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            shell=False,  # nosec B603
            timeout=30,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    with tempfile.TemporaryDirectory(prefix="transitstress-scan-") as temporary:
        target_root = Path(temporary).resolve()
        for name in sorted(set(listing) - {""}):
            source = ROOT / name
            target = target_root / name
            if not source.exists() and not source.is_symlink():
                continue  # tracked deletion has no working-tree bytes to scan
            if (
                source.is_symlink()
                or not source.is_file()
                or not source.resolve().is_relative_to(ROOT)
                or not target.resolve().is_relative_to(target_root)
                or any(parent.is_symlink() for parent in source.parents)
            ):
                print("INCOMPLETE: unsafe candidate path rejected (path withheld)")
                return 2
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        # Never expose scanner output, even redacted source lines; report only status.
        status = run(
            [str(executable), "dir", str(target_root), "--redact", "--no-banner"], quiet=True
        )
        print("Gitleaks: " + ("PASS" if status == 0 else "FAILED/INCOMPLETE; inspect locally"))
        return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--level",
        required=True,
        choices=["quick", "core", "ui", "security", "security-online", "release"],
    )
    level = parser.parse_args(argv).level
    codes = []
    if level in {"quick", "core", "release"}:
        codes.extend(
            [
                run([PYTHON, "-m", "ruff", "check", "."]),
                run([PYTHON, "-m", "ruff", "format", "--check", "."]),
                suite("tests/unit"),
            ]
        )
    if level in {"core", "release"}:
        codes.append(suite("tests/integration"))
    if level in {"ui", "release"}:
        codes.append(suite("tests/app"))
    if level in {"security", "release"}:
        codes.append(suite("tests/security"))
        sources = ["src", "scripts", "devtools"] + (
            ["app.py"] if (ROOT / "app.py").is_file() else []
        )
        codes.append(run([PYTHON, "-m", "bandit", "-r", *sources]))
        codes.append(scan_secrets())
    if level == "security-online":
        # T00 installs only this project as editable; third-party editables are prohibited.
        codes.append(run([PYTHON, "-m", "pip_audit", "--skip-editable"], timeout=600))
    if level == "release":
        codes.append(run([PYTHON, "-m", "transitstress.ui.bundle"]))
    if any(codes):
        return 1 if any(code not in (0, 2) for code in codes) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
