"""T01 configuration and identity boundary regressions; all data are harmless."""

import copy
import tomllib
from pathlib import Path

import pytest

from transitstress.cli import main
from transitstress.config import validate_config
from transitstress.errors import ConfigurationError


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("search", "injection_period_days", 3.41),
        ("search", "truth_mask", [True]),
        ("search", "maximum_period_samples", 999999999),
        ("execution", "allow_network_during_analysis", True),
        ("execution", "workers", 100),
        ("execution", "resume_only_matching_run_identity", False),
        ("preparation", "post_injection_sigma_clip", True),
        ("preparation", "interpolate_missing", True),
        ("preparation", "inject_after_binning", False),
        ("detrending", "normalize_output_again", True),
        ("limits", "maximum_period_samples", 50001),
        ("limits", "maximum_csv_bytes", 5242881),
        ("limits", "maximum_result_rows", 1001),
        ("limits", "maximum_archive_product_bytes", 52428801),
        ("limits", "maximum_acquisition_bytes", 1073741825),
    ],
)
def test_direct_config_rejects_truth_unsafe_flags_and_resource_overrides(section, key, value):
    raw = tomllib.loads(Path("configs/protocol_v1.toml").read_text(encoding="utf-8"))
    changed = copy.deepcopy(raw)
    changed[section][key] = value
    with pytest.raises(ConfigurationError):
        validate_config(changed)
    assert raw != changed


def test_invalid_cli_config_nonzero_without_traceback(tmp_path, capsys):
    text = Path("configs/protocol_v1.toml").read_text(encoding="utf-8")
    path = tmp_path / "invalid.toml"
    path.write_text(text.replace('method = "fast"', 'method = "unsupported"'), encoding="utf-8")
    assert main(["doctor", "--config", str(path)]) == 2
    output = capsys.readouterr()
    assert "config.search.method" in output.err
    assert "Traceback" not in output.err and not output.out
