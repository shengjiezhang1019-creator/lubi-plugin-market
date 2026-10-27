import pytest
from market.common import read_json, write_json
from market.validation import manifest_check
from pathlib import Path


def source(tmp_path):
    example = Path(__file__).resolve().parents[1] / "examples/demo_plugin/_manifest.json"
    manifest = read_json(example)
    write_json(tmp_path / "_manifest.json", manifest)
    (tmp_path / "plugin.py").write_text("# plugin", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")
    return {"plugin_name": manifest["name"], "tag": "v" + manifest["version"]}


def test_default_config_allowed(tmp_path):
    request = source(tmp_path)
    (tmp_path / "config.toml").write_text(
        'count = 3\napi_key = ""\ntoken = "YOUR_TOKEN"\npassword = ""\n', encoding="utf-8"
    )
    assert manifest_check(tmp_path, request)["name"] == request["plugin_name"]


def test_env_still_rejected(tmp_path):
    request = source(tmp_path)
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    with pytest.raises(ValueError, match=".env"):
        manifest_check(tmp_path, request)


def test_invalid_config_rejected(tmp_path):
    request = source(tmp_path)
    (tmp_path / "config.toml").write_text("[broken", encoding="utf-8")
    with pytest.raises(ValueError):
        manifest_check(tmp_path, request)
