"""Install the already reviewed Gitleaks binary; never called by analysis or tests."""

import argparse
import hashlib
import io
import json
import os
import platform
import stat
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSION = "8.30.1"
ARCHIVE_LIMIT = 32 * 1024 * 1024
BINARY_LIMIT = 32 * 1024 * 1024
HOSTS = {"github.com", "release-assets.githubusercontent.com"}
ASSETS = {
    "Linux": ("gitleaks_8.30.1_linux_x64.tar.gz", "gitleaks"),
    "Windows": ("gitleaks_8.30.1_windows_x64.zip", "gitleaks.exe"),
}


def validate_url(url: str) -> None:
    """Permit only the fixed release service and its HTTPS asset redirects."""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in HOSTS
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("release URL/redirect is outside the reviewed HTTPS hosts")


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(archive: str) -> bytes:
    """Fetch one fixed public asset with bounded bytes, time and redirects."""
    if archive not in {value[0] for value in ASSETS.values()}:
        raise ValueError("unsupported release asset")
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/{archive}"
    validate_url(url)
    opener = urllib.request.build_opener(ReleaseRedirect())
    started = time.monotonic()
    # Fixed reviewed HTTPS URL; every redirect is checked before following.
    with opener.open(url, timeout=30) as response:  # nosec B310
        validate_url(response.geturl())
        declared = response.headers.get("Content-Length")
        if declared is not None and not 0 < int(declared) <= ARCHIVE_LIMIT:
            raise ValueError("release asset declared size exceeds limit")
        chunks = []
        size = 0
        while True:
            if time.monotonic() - started > 120:
                raise ValueError("release asset download exceeded time budget")
            block = response.read(min(65536, ARCHIVE_LIMIT + 1 - size))
            if not block:
                break
            size += len(block)
            if size > ARCHIVE_LIMIT:
                raise ValueError("release asset actual size exceeds limit")
            chunks.append(block)
    return b"".join(chunks)


def executable_bytes(payload: bytes, archive: str, filename: str, pin: dict) -> bytes:
    """Verify the archive before reading only its bounded regular binary member."""
    if len(payload) > ARCHIVE_LIMIT:
        raise ValueError("archive exceeds limit")
    if hashlib.sha256(payload).hexdigest() != pin["archive_sha256"]:
        raise ValueError("archive checksum mismatch")
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            members = [entry for entry in bundle.infolist() if entry.filename == filename]
            if len(members) != 1:
                raise ValueError("binary member must occur exactly once")
            member = members[0]
            mode = member.external_attr >> 16
            if (
                member.is_dir()
                or stat.S_ISLNK(mode)
                or member.flag_bits & 1
                or not 0 < member.file_size <= BINARY_LIMIT
            ):
                raise ValueError("invalid binary member")
            result = bundle.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
            members = [entry for entry in bundle.getmembers() if entry.name == filename]
            if len(members) != 1:
                raise ValueError("binary member must occur exactly once")
            member = members[0]
            if not member.isfile() or not 0 < member.size <= BINARY_LIMIT:
                raise ValueError("invalid binary member")
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError("missing binary payload")
            with stream:
                result = stream.read(BINARY_LIMIT + 1)
    if not 0 < len(result) <= BINARY_LIMIT:
        raise ValueError("binary size exceeds limit")
    if hashlib.sha256(result).hexdigest() != pin["executable_sha256"]:
        raise ValueError("binary checksum mismatch")
    return result


def install(archive_path: Path | None = None) -> None:
    """Restore the platform pin from a local archive, or explicit network setup."""
    system = platform.system()
    if system not in ASSETS or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("only reviewed Linux/Windows x86-64 platforms are supported")
    archive, filename = ASSETS[system]
    pins = json.loads((ROOT / "scripts/gitleaks-pin.json").read_text())
    pin = pins["platforms"][f"{system}-x86_64"]
    if pins["version"] != VERSION or pin["archive"] != archive or pin["filename"] != filename:
        raise ValueError("platform pin does not match reviewed asset")
    directory = ROOT / ".tools"
    if directory.is_symlink() or not directory.resolve().is_relative_to(ROOT):
        raise ValueError("tool directory must remain within this checkout")
    directory.mkdir(exist_ok=True)
    destination = directory / filename
    if destination.is_symlink():
        raise ValueError("binary destination must not be a symlink")
    if destination.exists():
        if not destination.is_file() or destination.stat().st_size > BINARY_LIMIT:
            raise ValueError("existing tool is not a bounded regular file")
        if hashlib.sha256(destination.read_bytes()).hexdigest() != pin["executable_sha256"]:
            raise ValueError("existing tool mismatch; preserve and investigate it")
        print("Gitleaks pinned executable already present")
        return
    if archive_path is None:
        payload = download(archive)
    else:
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError("archive path must be a regular local file")
        if archive_path.stat().st_size > ARCHIVE_LIMIT:
            raise ValueError("local archive exceeds limit")
        with archive_path.open("rb") as source:
            payload = source.read(ARCHIVE_LIMIT + 1)
    binary = executable_bytes(payload, archive, filename, pin)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as target:
            temporary = Path(target.name)
            target.write(binary)
        temporary.chmod(0o755)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("Gitleaks pinned executable installed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path, help="Existing verified release archive; offline")
    source.add_argument(
        "--download", action="store_true", help="Explicit public tool setup network"
    )
    args = parser.parse_args()
    try:
        install(args.archive)
    except (
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        zipfile.BadZipFile,
        urllib.error.URLError,
    ):
        print("INCOMPLETE: pinned Gitleaks setup failed; check platform, asset and digest locally")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
