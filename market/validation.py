from __future__ import annotations

import re
from pathlib import Path

from jsonschema import Draft202012Validator
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from .common import NAME, digest, object_digest, read_json
from .rules.static_checks import _scan_python_files
from .rules.component_types import analyze_component_types
from .rules.static_checks import CheckResult, Status

RULES_DIR = Path(__file__).resolve().parent / "rules"
REVIEW_PROMPT = Path(__file__).resolve().parents[1] / "prompts/plugin-review.md"


def rules_fingerprint():
    """将本仓库规则和校验入口内容绑定到报告，不依赖主程序 commit。"""
    files = [
        RULES_DIR / "__init__.py",
        RULES_DIR / "manifest_schema.json",
        RULES_DIR / "static_checks.py",
        RULES_DIR / "component_types.py",
        Path(__file__).resolve(),
        REVIEW_PROMPT,
        Path(__file__).resolve().parent / "relisting.py",
    ]
    return object_digest({path.name: digest(path.read_bytes()) for path in files})


def framework_check(source, *, component_analysis=None):
    """使用市场自带 schema 和 AST 规则，不导入主程序或待审插件。"""
    manifest = read_json(Path(source) / "_manifest.json")
    schema = read_json(RULES_DIR / "manifest_schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError("manifest 不符合市场规则: " + "; ".join(e.message for e in errors))
    results = _scan_python_files(Path(source))
    analysis = component_analysis if component_analysis is not None else analyze_component_types(Path(source))
    message = "静态识别组件类型：" + (", ".join(analysis["types"]) or "未发现")
    if analysis["status"] == "unknown":
        message += "；无法完全静态确认，需人工核实：" + "；".join(analysis["warnings"])
    results.append(CheckResult("component_types", Status.WARN if analysis["status"] == "unknown" else Status.PASS,
                               message))
    return [{"name": r.name, "status": r.status.value, "message": r.message, "location": r.location} for r in results]


def manifest_check(source, request):
    source = Path(source)
    for file in ("_manifest.json", "plugin.py", "README.md"):
        if not (source / file).is_file():
            raise ValueError(f"缺少必需文件 {file}")
    manifest = read_json(source / "_manifest.json")
    if manifest.get("name") != request["plugin_name"] or not NAME.fullmatch(manifest["name"]):
        raise ValueError("manifest name 与申请不一致")
    if manifest.get("version") != request["tag"][1:]:
        raise ValueError("manifest version 与 Release 标签不一致")
    dependencies = manifest.get("python_dependencies", [])
    if not isinstance(dependencies, list):
        raise ValueError("python_dependencies 必须为数组")
    seen = set()
    for item in dependencies:
        if not isinstance(item, dict) or set(item) != {"name", "version"}:
            raise ValueError("Python 依赖项只能含 name、version")
        if not isinstance(item["name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item["name"]):
            raise ValueError("Python 依赖仅允许包名，不允许 URL、pip 参数或本地路径")
        name = canonicalize_name(item["name"])
        if name in seen:
            raise ValueError("Python 依赖名称重复")
        seen.add(name)
        if not isinstance(item["version"], str) or not item["version"].strip():
            raise ValueError("Python 依赖需要声明实际测试的版本范围")
        SpecifierSet(item["version"])
    if (source / "config.toml").exists() or (source / ".env").exists():
        raise ValueError("发布源码不能包含真实 config.toml 或 .env，请使用配置模型或模板")
    return manifest
