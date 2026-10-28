"""本仓库规则可独立运行，不依赖主程序 checkout，并绑定报告。"""

import io
import zipfile
from pathlib import Path

import pytest

from market.common import read_json, write_json
from market.pipeline import validate
from market.validation import framework_check, rules_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def plugin(tmp_path, manifest):
    write_json(tmp_path / "_manifest.json", manifest)
    (tmp_path / "plugin.py").write_text("from src.plugin_system import BasePlugin\n", encoding="utf-8")
    return manifest


def test_valid_manifest_and_code_need_no_host_checkout(tmp_path, monkeypatch, plugin_manifest):
    import subprocess

    plugin(tmp_path, plugin_manifest)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("不能拉取或查询主程序 Git"))
    assert not any(check["status"] == "fail" for check in framework_check(tmp_path))


def test_invalid_python_dependency_shape_is_rejected(tmp_path, plugin_manifest):
    manifest = plugin(tmp_path, plugin_manifest)
    manifest["python_dependencies"] = ["httpx"]
    write_json(tmp_path / "_manifest.json", manifest)
    with pytest.raises(ValueError, match="manifest"):
        framework_check(tmp_path)


@pytest.mark.parametrize("code", ["import subprocess", "eval('1')", "def invalid(:"])
def test_static_rules_reject_dangerous_or_invalid_code_without_executing(tmp_path, code, plugin_manifest):
    plugin(tmp_path, plugin_manifest)
    (tmp_path / "plugin.py").write_text(code, encoding="utf-8")
    assert any(check["status"] == "fail" for check in framework_check(tmp_path))


def test_report_binds_bundled_rules_without_external_host(tmp_path, plugin_manifest):
    root = tmp_path / "market"
    write_json(root / "registry.json", {"plugins": {}})
    source = tmp_path / "source"
    source.mkdir()
    plugin(source, plugin_manifest)
    (source / "README.md").write_text("test", encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for file in source.rglob("*"):
            if file.is_file():
                archive.write(file, "source/" + file.relative_to(source).as_posix())
    request = {"plugin_name": plugin_manifest["name"], "repository": "https://github.com/example/test", "tag": "v1.0.0"}
    report, source = validate(root, request, tmp_path / "work", static_only=True,
                              archive_data=buffer.getvalue(), commit="a" * 40)
    assert report["status"] == "static_only"
    assert report["rules_fingerprint"] == rules_fingerprint()
    assert "host_commit" not in report


def test_changed_rule_content_changes_fingerprint(tmp_path, monkeypatch):
    from market import validation

    for name in ("__init__.py", "manifest_schema.json", "static_checks.py", "component_types.py", "hook_bindings.py"):
        (tmp_path / name).write_text("original", encoding="utf-8")
    monkeypatch.setattr(validation, "RULES_DIR", tmp_path)
    before = rules_fingerprint()
    (tmp_path / "manifest_schema.json").write_text("changed", encoding="utf-8")
    assert rules_fingerprint() != before


def test_workflow_does_not_checkout_external_host():
    workflow = (ROOT / ".github/workflows/market.yml").read_text(encoding="utf-8")
    assert "HOST_READ_TOKEN" not in workflow
    assert "--host" not in workflow
    assert "scripts/host_config.py" not in workflow
    assert "market.cli validate --work" in workflow


def test_market_and_host_do_not_require_component_type_declarations():
    host = Path("C:/AGI/src/plugin_system/core/loading/manifest_schema.json")
    if not host.is_file():
        pytest.skip("可选的一致性检查：当前机器没有露比主程序源码")
    market_schema = read_json(ROOT / "market/rules/manifest_schema.json")
    host_schema = read_json(host)
    assert "comp_types" not in market_schema["required"]
    assert "comp_types" not in market_schema["properties"]
    assert "comp_types" not in host_schema["properties"]
    assert "comp_types" not in host_schema["required"]
    assert market_schema == host_schema
