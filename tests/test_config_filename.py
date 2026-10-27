import pytest
from market.rules.static_checks import _scan_python_files


@pytest.mark.parametrize("name", ["", "config.toml", "settings.toml", "sub/config.toml", None])
def test_config_filename_rule(tmp_path, name):
    (tmp_path / "plugin.py").write_text(f"class Plugin:\n    config_file_name = {name!r}\n", encoding="utf-8")
    failures = [r for r in _scan_python_files(tmp_path) if r.name == "config_file_name" and r.status.value == "fail"]
    assert bool(failures) is (name not in ("", "config.toml"))
