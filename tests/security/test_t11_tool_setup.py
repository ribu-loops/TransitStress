"""Independent malicious archive/URL fixtures for the CI tool-install boundary."""

import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from devtools import install_gitleaks as tool


def pin(payload, binary=b"fixture executable"):
    return {
        "archive_sha256": hashlib.sha256(payload).hexdigest(),
        "executable_sha256": hashlib.sha256(binary).hexdigest(),
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/asset",
        "https://github.com.evil.invalid/asset",
        "https://127.0.0.1/asset",
        "https://release-assets.githubusercontent.com@evil.invalid/asset",
        "https://github.com:444/asset",
        "https://user:password@github.com/asset",
        "file:///tmp/asset",
    ],
)
def test_redirect_denied_before_following(url):
    with pytest.raises(ValueError):
        tool.ReleaseRedirect().redirect_request(None, None, 302, "", {}, url)


def test_digest_precedes_archive_parsing(monkeypatch):
    monkeypatch.setattr(tool.zipfile, "ZipFile", lambda *a: pytest.fail("parsed unverified bytes"))
    with pytest.raises(ValueError, match="checksum"):
        tool.executable_bytes(b"untrusted", "asset.zip", "gitleaks.exe", pin(b"trusted"))


def test_zip_duplicate_and_symlink_never_written():
    for symbolic in [False, True]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            member = zipfile.ZipInfo("gitleaks.exe")
            member.external_attr = (0o120777 if symbolic else 0o100755) << 16
            bundle.writestr(member, b"fixture executable")
            if not symbolic:
                with pytest.warns(UserWarning):
                    bundle.writestr("gitleaks.exe", b"fixture executable")
        payload = buffer.getvalue()
        with pytest.raises(ValueError):
            tool.executable_bytes(payload, "asset.zip", "gitleaks.exe", pin(payload))


def test_tar_link_rejected_and_other_members_not_extracted(tmp_path):
    for link in [False, True]:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            member = tarfile.TarInfo("gitleaks")
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
                bundle.addfile(member)
            else:
                member.size = len(b"fixture executable")
                bundle.addfile(member, io.BytesIO(b"fixture executable"))
            other = tarfile.TarInfo("../../outside")
            other.size = 3
            bundle.addfile(other, io.BytesIO(b"bad"))
        payload = buffer.getvalue()
        if link:
            with pytest.raises(ValueError):
                tool.executable_bytes(payload, "asset.tar.gz", "gitleaks", pin(payload))
        else:
            assert tool.executable_bytes(payload, "asset.tar.gz", "gitleaks", pin(payload)) == (
                b"fixture executable"
            )
        assert list(tmp_path.iterdir()) == []


def test_binary_digest_and_member_size_are_independent_checks(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("gitleaks.exe", b"fixture executable")
    payload = buffer.getvalue()
    with pytest.raises(ValueError, match="binary checksum"):
        tool.executable_bytes(payload, "asset.zip", "gitleaks.exe", pin(payload, b"other"))
    monkeypatch.setattr(tool, "BINARY_LIMIT", 2)
    with pytest.raises(ValueError, match="invalid binary"):
        tool.executable_bytes(payload, "asset.zip", "gitleaks.exe", pin(payload))


def test_actual_download_limit_without_network(monkeypatch):
    class Response(io.BytesIO):
        headers = {}

        def geturl(self):
            return "https://release-assets.githubusercontent.com/asset"

    class Opener:
        def open(self, url, timeout):
            assert url.startswith("https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/")
            assert timeout == 30
            return Response(b"12345")

    monkeypatch.setattr(tool, "ARCHIVE_LIMIT", 4)
    monkeypatch.setattr(tool.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(ValueError, match="actual size"):
        tool.download("gitleaks_8.30.1_linux_x64.tar.gz")


def test_tool_directory_escape_and_existing_mismatch_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tool.platform, "machine", lambda: "x86_64")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/gitleaks-pin.json").write_text(
        json.dumps(
            {
                "version": "8.30.1",
                "platforms": {
                    "Linux-x86_64": {
                        "archive": "gitleaks_8.30.1_linux_x64.tar.gz",
                        "filename": "gitleaks",
                        "executable_sha256": hashlib.sha256(b"trusted").hexdigest(),
                    }
                },
            }
        )
    )
    monkeypatch.setattr(tool, "download", lambda *a: pytest.fail("network before path validation"))
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".tools").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Native symlink creation unavailable; this boundary is not proved here")
    with pytest.raises(ValueError, match="directory"):
        tool.install()
    (tmp_path / ".tools").unlink()
    (tmp_path / ".tools").mkdir()
    (tmp_path / ".tools/gitleaks").write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="existing tool mismatch"):
        tool.install()
    assert (tmp_path / ".tools/gitleaks").read_bytes() == b"untrusted"


def test_release_scanner_covers_new_development_code(monkeypatch):
    from scripts import check

    calls = []
    monkeypatch.setattr(check, "suite", lambda *args: 0)
    monkeypatch.setattr(check, "scan_secrets", lambda: 0)
    monkeypatch.setattr(check, "run", lambda args, **kwargs: calls.append(args) or 0)
    assert check.main(["--level", "security"]) == 0
    bandit = next(args for args in calls if "bandit" in args)
    assert "devtools" in bandit
