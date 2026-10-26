"""组件分类只解析源码，不依赖 manifest 声明或执行插件。"""
import io
import zipfile

import pytest

from market.common import object_digest, read_json, write_json
from market.index import approve, build_catalog
from market.pipeline import validate
from market.rules.component_types import analyze_component_types
from market.validation import framework_check


def package(root, files, **extra):
    write_json(root / "_manifest.json", {"name": "sample", "version": "0.1.0", "description": "test",
               "author": "test", "license": "MIT", "plugin_dependencies": [], **extra})
    (root / "README.md").write_text("test", encoding="utf-8")
    for name, code in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    return root


@pytest.mark.parametrize("base,kind", [("BaseCommand", "command"), ("BaseTool", "tool"), ("BaseEvent", "event")])
def test_infers_alias_without_declaration_or_execution(tmp_path, base, kind):
    package(tmp_path, {"plugin.py": f"from src.plugin_system import {base} as Parent\n"
                     "raise RuntimeError('must never execute')\nclass Sample(Parent): pass"})
    assert analyze_component_types(tmp_path) == {"types": [kind], "status": "detected", "warnings": []}
    assert not any(r["status"] == "fail" for r in framework_check(tmp_path))


def test_relative_import_reexport_and_indirect_inheritance(tmp_path):
    package(tmp_path, {
        "bases.py": "import src.plugin_system.base.component_contexts as ctx\nclass Parent(ctx.BaseCommand): pass",
        "components/__init__.py": "from ..bases import Parent as Exported",
        "plugin.py": "from .components import Exported\nAlias = Exported\nclass Child(Alias): pass",
    })
    assert analyze_component_types(tmp_path) == {"types": ["command"], "status": "detected", "warnings": []}


@pytest.mark.parametrize("legacy", [None, ["tool"], ["hook"], "command", [42]])
def test_legacy_metadata_neither_blocks_nor_controls_types(tmp_path, legacy):
    package(tmp_path, {"plugin.py": "from src.plugin_system import BaseCommand\nclass Command(BaseCommand): pass"},
            **({"comp_types": legacy} if legacy is not None else {}))
    assert analyze_component_types(tmp_path)["types"] == ["command"]
    assert not any(r["status"] == "fail" for r in framework_check(tmp_path))


@pytest.mark.parametrize("code", [
    "class BaseCommand: pass\nclass Sample(BaseCommand): pass",
    "from external_library import Parent\nclass Sample(Parent): pass",
    "from src.plugin_system import BaseCommand\nCommand = type('Command', (BaseCommand,), {})",
    "from src.plugin_system import BasePlugin, LifecycleBindingRequest\n"
    "class Plugin(BasePlugin):\n def build_lifecycle_bindings(self):\n"
    "  return (LifecycleBindingRequest(point_id='ANY', component=make_component()),)",
])
def test_unknown_paths_warn_instead_of_claiming_no_components(tmp_path, code):
    package(tmp_path, {"plugin.py": code})
    result = analyze_component_types(tmp_path)
    assert result["types"] == []
    assert result["status"] == "unknown"
    assert result["warnings"]
    assert any(r["name"] == "component_types" and r["status"] == "warn" for r in framework_check(tmp_path))


def test_componentless_plugin_needs_no_declaration(tmp_path):
    package(tmp_path, {"plugin.py": "from src.plugin_system import BasePlugin\nclass Plugin(BasePlugin): pass"})
    assert analyze_component_types(tmp_path) == {"types": [], "status": "detected", "warnings": []}


def test_mixed_types_preserved_with_unknown_dynamic_path(tmp_path):
    package(tmp_path, {"plugin.py": "from src.plugin_system import BaseCommand, BaseTool\n"
                     "class Command(BaseCommand): pass\nclass Tool(BaseTool): pass\nDynamic = type('Dynamic', (), {})"})
    result = analyze_component_types(tmp_path)
    assert result["types"] == ["command", "tool"]
    assert result["status"] == "unknown"


def test_normal_type_check_is_not_a_dynamic_factory(tmp_path):
    package(tmp_path, {"plugin.py": "from src.plugin_system import BaseCommand\nclass Command(BaseCommand): pass\n"
                     "def validate(value): return type(value) is int"})
    assert analyze_component_types(tmp_path)["status"] == "detected"


def test_syntax_error_still_blocks(tmp_path):
    package(tmp_path, {"plugin.py": "class Broken(:"})
    assert analyze_component_types(tmp_path)["status"] == "unknown"
    assert any(r["status"] == "fail" for r in framework_check(tmp_path))


def test_validation_approval_and_catalog_use_source_analysis(tmp_path):
    root = tmp_path / "market"
    write_json(root / "registry.json", {"plugins": {}})
    source = package(tmp_path / "plugin", {"plugin.py": "from src.plugin_system import BaseCommand\n"
                     "class Command(BaseCommand): pass"}, comp_types=["tool"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for file in source.iterdir():
            archive.write(file, "sample/" + file.name)
    request = {"plugin_name": "sample", "repository": "https://github.com/example/sample", "tag": "v0.1.0"}
    report, _ = validate(root, request, tmp_path / "work", archive_data=buffer.getvalue(), commit="a" * 40)
    assert report["component_analysis"]["types"] == ["command"]
    canonical = dict(report)
    fingerprint = canonical.pop("fingerprint")
    assert fingerprint == object_digest(canonical)
    approve(root, report, {"status": "completed", "commit": report["commit"],
                          "validation_fingerprint": fingerprint}, "maintainer", 1)
    assert read_json(root / "catalog.json")[0]["comp_types"] == ["command"]


def test_historical_record_does_not_fall_back_to_author_declaration(tmp_path):
    request = {"plugin_name": "sample", "repository": "https://github.com/example/sample"}
    write_json(tmp_path / "registry.json", {"plugins": {"sample": {"repository": request["repository"]}}})
    write_json(tmp_path / "versions/sample/0.1.0.json", {"status": "approved", "validation": {
        "request": request, "manifest": {"version": "0.1.0", "comp_types": ["tool"]},
        "commit": "a" * 40, "sha256": "b" * 64, "release_notes": "", "published_at": "",
    }})
    assert build_catalog(tmp_path)[0]["comp_types"] == []
