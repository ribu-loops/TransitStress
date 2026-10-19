import subprocess

from scripts import check


def test_failed_command_stays_nonzero(monkeypatch):
    def failed(*args, **kwargs):
        assert kwargs["check"] is False
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 300
        return subprocess.CompletedProcess(args[0], 7)

    monkeypatch.setattr(check.subprocess, "run", failed)
    assert check.run([check.PYTHON, "-m", "ruff", "check", "."]) == 7


def test_missing_suite_incomplete(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check, "ROOT", tmp_path)
    assert check.suite("tests/integration") == 2
    assert "INCOMPLETE" in capsys.readouterr().out


def test_main_does_not_hide_required_failure(monkeypatch):
    monkeypatch.setattr(check, "run", lambda *args, **kwargs: 7)
    monkeypatch.setattr(check, "suite", lambda *args: 0)
    assert check.main(["--level", "quick"]) != 0
