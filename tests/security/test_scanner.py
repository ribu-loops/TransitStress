import hashlib
import json
import subprocess

import pytest

from scripts import check


@pytest.mark.parametrize("system,filename", [("Windows", "gitleaks.exe"), ("Linux", "gitleaks")])
def test_scanner_rejects_path_escape(tmp_path, monkeypatch, capsys, system, filename):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / ".tools").mkdir()
    (root / ".tools" / filename).write_bytes(b"nonexecutable-test-fixture")
    digest = hashlib.sha256(b"nonexecutable-test-fixture").hexdigest()
    (root / "scripts/gitleaks-pin.json").write_text(
        json.dumps(
            {"platforms": {f"{system}-x86_64": {"filename": filename, "executable_sha256": digest}}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "outside.txt").write_text("harmless outside fixture", encoding="utf-8")
    monkeypatch.setattr(check.platform, "system", lambda: system)
    monkeypatch.setattr(check.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(check, "ROOT", root)
    monkeypatch.setattr(check.shutil, "which", lambda name: "git")

    def listing(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=b"../outside.txt\0")

    monkeypatch.setattr(check.subprocess, "run", listing)
    assert check.scan_secrets() == 2
    assert "unsafe candidate path" in capsys.readouterr().out


def test_quiet_scanner_output_is_not_printed(monkeypatch, capsys):
    def failed(*args, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(args[0], 1, stdout="harmless-sensitive-canary")

    monkeypatch.setattr(check.subprocess, "run", failed)
    assert check.run(["fixed-test-command"], quiet=True) == 1
    assert "harmless-sensitive-canary" not in capsys.readouterr().out


def test_missing_scanner_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "ROOT", tmp_path)
    assert check.scan_secrets() == 2


@pytest.mark.parametrize("system,filename", [("Windows", "gitleaks.exe"), ("Linux", "gitleaks")])
def test_platform_pin_coverage_and_failure_propagation(tmp_path, monkeypatch, system, filename):
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".tools").mkdir()
    executable = tmp_path / ".tools" / filename
    executable.write_bytes(b"test-only-bytes")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (tmp_path / "scripts/gitleaks-pin.json").write_text(
        json.dumps(
            {"platforms": {f"{system}-x86_64": {"filename": filename, "executable_sha256": digest}}}
        )
    )
    report = tmp_path / "reports/new-review.md"
    report.parent.mkdir()
    report.write_text("untracked report fixture")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check.platform, "system", lambda: system)
    monkeypatch.setattr(check.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(check.shutil, "which", lambda name: "git")
    monkeypatch.setattr(
        check.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=b"reports/new-review.md\0"),
    )
    calls = []

    def scan(arguments, quiet=False):
        calls.append(arguments)
        assert quiet and arguments[0] == str(executable)
        assert arguments[1] == "dir" and "--redact" in arguments
        assert (
            check.Path(arguments[2]) / "reports/new-review.md"
        ).read_bytes() == report.read_bytes()
        return 1

    monkeypatch.setattr(check, "run", scan)
    assert check.scan_secrets() == 1
    assert len(calls) == 1
    executable.write_bytes(b"changed")
    assert check.scan_secrets() == 2
    assert len(calls) == 1
    monkeypatch.setattr(check.platform, "system", lambda: "Unsupported")
    assert check.scan_secrets() == 2
    assert len(calls) == 1
