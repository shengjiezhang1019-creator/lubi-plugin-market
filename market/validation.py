from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from jsonschema import Draft202012Validator
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from .common import COMMIT, NAME, read_json


def framework_check(source, host, expected_commit):
    """直接复用固定主程序中的 schema 与纯 AST 检查函数，不导入完整主程序或插件。"""
    host = Path(host).resolve()
    actual = subprocess.run(["git", "-C", str(host), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    if not COMMIT.fullmatch(expected_commit) or actual != expected_commit:
        raise ValueError("检查工具 checkout 与固定 host_commit 不一致")
    files = ["src/plugin_system/market/validation.py", "src/plugin_system/core/loading/manifest_schema.json"]
    for file in files:
        subprocess.run(["git", "-C", str(host), "diff", "--exit-code", "HEAD", "--", file], check=True, capture_output=True)
    manifest = read_json(Path(source) / "_manifest.json")
    schema = read_json(host / files[1])
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError("manifest 不符合框架 schema: " + "; ".join(e.message for e in errors))
    tree = ast.parse((host / files[0]).read_text(encoding="utf-8"))
    names = {"Status", "CheckResult", "_scan_python_files", "_imported_modules", "_classify_import", "_dangerous_call"}
    selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    if {node.name for node in selected} != names:
        raise ValueError("固定检查工具接口已变化，需要维护者适配，不能跳过")
    namespace = {"__name__": __name__, "ast": ast, "sys": sys, "Path": Path, "dataclass": dataclass, "Enum": Enum, "Optional": Optional}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"_DANGEROUS_STDLIB", "_DANGEROUS_CALLS"}:
                    namespace[target.id] = ast.literal_eval(node.value)
    exec(compile(ast.Module(body=selected, type_ignores=[]), "fixed-framework-static-checks", "exec"), namespace)
    results = namespace["_scan_python_files"](Path(source))
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
