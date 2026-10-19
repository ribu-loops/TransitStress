import json

from transitstress.cli import main


def test_doctor_environment_canary(monkeypatch, capsys):
    canary = "harmless-private-canary-9381"
    monkeypatch.setenv("TRANSITSTRESS_TEST_PRIVATE", canary)
    monkeypatch.delenv("TRANSITSTRESS_MODE", raising=False)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert canary not in output
    assert "TRANSITSTRESS_TEST_PRIVATE" not in output
    assert json.loads(output)["mode"] == "demo"


def test_native_streamlit_protections():
    from streamlit import config

    expected = {
        "server.enableCORS": True,
        "server.enableXsrfProtection": True,
        "server.maxUploadSize": 5,
        "browser.gatherUsageStats": False,
        "client.showErrorDetails": "none",
    }
    for key, value in expected.items():
        assert config.get_option(key) == value
