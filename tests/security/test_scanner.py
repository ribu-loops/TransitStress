import hashlib
import json
import subprocess

from scripts import check


def test_scanner_rejects_path_escape(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / ".tools").mkdir()
    (root / ".tools/gitleaks.exe").write_bytes(b"nonexecutable-test-fixture")
    digest = hashlib.sha256(b"nonexecutable-test-fixture").hexdigest()
    (root / "scripts/gitleaks-pin.json").write_text(
        json.dumps({"executable_sha256": digest}), encoding="utf-8"
    )
    (tmp_path / "outside.txt").write_text("harmless outside fixture", encoding="utf-8")
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
