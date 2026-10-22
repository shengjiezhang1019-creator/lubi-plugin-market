"""组件类型校验必须离线且不执行插件；不依赖露比源码。"""

import json

import pytest

from market.rules.component_types import check_component_types
from market.validation import framework_check


def package(tmp_path, kinds, files):
    manifest = {
        "name": "sample",
        "version": "0.1.0",
        "description": "test",
        "author": "test",
        "license": "MIT",
        "plugin_dependencies": [],
    }
    if kinds is not None:
        manifest["comp_types"] = kinds
    (tmp_path / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, code in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("kinds", [None, ["hook"], ["COMMAND"], ["command", "command"], "command", [42]])
def test_missing_or_invalid_types_fail_schema(tmp_path, kinds):
    source = package(tmp_path, kinds, {"plugin.py": ""})
    with pytest.raises(ValueError, match="manifest"):
        framework_check(source)


@pytest.mark.parametrize("base,kind", [("BaseCommand", "command"), ("BaseTool", "tool"), ("BaseEvent", "event")])
def test_public_component_alias_is_detected_without_executing(tmp_path, base, kind):
    package(
        tmp_path,
        [kind],
        {
            "plugin.py": f"""
from src.plugin_system.base.component_contexts import {base} as Parent
raise RuntimeError('must never execute')
class Sample(Parent):
    pass
"""
        },
    )
    assert [r.status.value for r in check_component_types(tmp_path, [kind])] == ["pass"]
    result = check_component_types(tmp_path, [])
    assert any(r.status.value == "fail" and kind in r.message for r in result)


def test_module_alias_relative_import_and_indirect_inheritance(tmp_path):
    package(
        tmp_path,
        ["command"],
        {
            "bases.py": "import src.plugin_system.base.component_contexts as ctx\nclass Parent(ctx.BaseCommand): pass\n",
            "commands.py": "from .bases import Parent as LocalParent\nclass Child(LocalParent): pass\n",
            "plugin.py": "from .commands import Child\n",
        },
    )
    assert [r.status.value for r in check_component_types(tmp_path, ["command"])] == ["pass"]


def test_reexported_class_and_assignment_alias(tmp_path):
    package(
        tmp_path,
        ["tool"],
        {
            "tools.py": "from src.plugin_system.base.component_contexts import BaseTool\nAlias = BaseTool\nclass Tool(Alias): pass",
            "public.py": "from .tools import Tool as Exported",
            "plugin.py": "from .public import Exported\nclass Child(Exported): pass",
        },
    )
    assert [r.status.value for r in check_component_types(tmp_path, ["tool"])] == ["pass"]


def test_wrong_extra_type_is_rejected_when_source_is_fully_known(tmp_path):
    package(
        tmp_path,
        ["tool"],
        {
            "plugin.py": "from src.plugin_system.base.component_contexts import BaseCommand\nclass Command(BaseCommand): pass"
        },
    )
    results = check_component_types(tmp_path, ["tool"])
    assert len([r for r in results if r.status.value == "fail"]) == 2


def test_explicit_empty_types_allowed_for_componentless_plugin(tmp_path):
    source = package(
        tmp_path,
        [],
        {"plugin.py": "from src.plugin_system.base.base_plugin import BasePlugin\nclass Plugin(BasePlugin): pass"},
    )
    assert not any(r["status"] == "fail" for r in framework_check(source))


def test_unrelated_name_does_not_impersonate_public_base(tmp_path):
    package(tmp_path, ["command"], {"plugin.py": "class BaseCommand: pass\nclass Sample(BaseCommand): pass"})
    results = check_component_types(tmp_path, ["command"])
    assert not any(r.status.value == "pass" for r in results)
    assert any(r.status.value == "warn" for r in results)


def test_unknown_external_base_is_flagged_for_manual_review(tmp_path):
    package(tmp_path, ["command"], {"plugin.py": "from some_library import Parent\nclass Sample(Parent): pass"})
    assert [r.status.value for r in check_component_types(tmp_path, ["command"])] == ["warn"]


def test_dynamic_binding_is_not_claimed_as_verified(tmp_path):
    package(
        tmp_path,
        ["command"],
        {
            "plugin.py": """
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.mount_point_types import LifecycleBindingRequest
from .factory import make_component
class Plugin(BasePlugin):
    def build_lifecycle_bindings(self):
        return (LifecycleBindingRequest(point_id='ANY', component=make_component()),)
"""
        },
    )
    assert [r.status.value for r in check_component_types(tmp_path, ["command"])] == ["warn"]


def test_declared_type_without_component_is_not_accepted(tmp_path):
    package(tmp_path, ["command"], {"plugin.py": ""})
    assert [r.status.value for r in check_component_types(tmp_path, ["command"])] == ["fail"]


def test_normal_type_validation_does_not_trigger_dynamic_component_warning(tmp_path):
    package(
        tmp_path,
        ["command"],
        {
            "plugin.py": """
from src.plugin_system.base.component_contexts import BaseCommand
class Command(BaseCommand): pass
def validate(value):
    return type(value) is int
"""
        },
    )
    assert [r.status.value for r in check_component_types(tmp_path, ["command"])] == ["pass"]


def test_dynamic_type_factory_requires_review(tmp_path):
    package(
        tmp_path,
        ["command"],
        {
            "plugin.py": """
from src.plugin_system.base.component_contexts import BaseCommand
Command = type('Command', (BaseCommand,), {})
"""
        },
    )
    assert [r.status.value for r in check_component_types(tmp_path, ["command"])] == ["warn"]
