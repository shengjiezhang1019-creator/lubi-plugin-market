"""市场静态规则，初始同步自露比；本模块仅解析 AST，不执行待审插件。"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

_DANGEROUS_STDLIB = {"os", "subprocess", "socket", "ctypes", "importlib", "sys"}


_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    location: Optional[str] = None


def _scan_python_files(pdir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    py_files = [p for p in sorted(pdir.rglob("*.py")) if "__pycache__" not in p.parts]
    if not py_files:
        return [CheckResult("python_files", Status.WARN, "插件目录下没有 .py 文件")]

    self_pkg = pdir.name
    import_issues: list[CheckResult] = []
    danger_issues: list[CheckResult] = []
    for py in py_files:
        rel = py.relative_to(pdir)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            results.append(CheckResult("syntax", Status.FAIL, f"语法错误: {exc.msg}", f"{rel}:{exc.lineno}"))
            continue
        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(target, ast.Name) and target.id == "config_file_name" for target in targets):
                if isinstance(node.value, ast.Constant):
                    if type(node.value.value) is not str or node.value.value not in ("", "config.toml"):
                        results.append(CheckResult("config_file_name", Status.FAIL,
                                                   "配置文件只能使用根目录 config.toml；不落盘时留空", f"{rel}:{node.lineno}"))
                elif node.value is not None:
                    results.append(CheckResult("config_file_name", Status.WARN,
                                               "动态配置文件名无法静态确认，宿主加载时仍只允许 config.toml 或空串", f"{rel}:{node.lineno}"))
            for module, lineno in _imported_modules(node):
                verdict = _classify_import(module, self_pkg)
                if verdict is not None:
                    status, msg = verdict
                    import_issues.append(CheckResult("import_allowlist", status, msg, f"{rel}:{lineno}"))
            called = _dangerous_call(node)
            if called:
                danger_issues.append(
                    CheckResult("dangerous_pattern", Status.FAIL, f"危险调用 {called}(...)", f"{rel}:{node.lineno}")
                )

    results.extend(import_issues or [CheckResult("import_allowlist", Status.PASS, "import 白名单：无越界")])
    results.extend(danger_issues or [CheckResult("dangerous_pattern", Status.PASS, "危险模式扫描：未发现")])
    return results


def _imported_modules(node) -> list[tuple[str, int]]:
    """产出 (绝对模块路径, 行号)；相对 import 视为插件自身，跳过。"""
    out: list[tuple[str, int]] = []
    if isinstance(node, ast.Import):
        out.extend((alias.name, node.lineno) for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return out  # 相对 import = 插件内部，放行
        if node.module:
            out.append((node.module, node.lineno))
    return out


def _classify_import(module: str, self_pkg: str):
    """返回 None=放行；(Status, msg)=有问题（WARN/FAIL）。"""
    parts = module.split(".")
    top = parts[0]
    if top in _DANGEROUS_STDLIB:
        return (Status.FAIL, f"禁止 import 危险标准库 {module}")
    if (
        module == "src.plugin_system"
        or module == "src.plugin_system.base"
        or module.startswith("src.plugin_system.base.")
    ):
        return None  # 插件系统公共面
    if module.startswith("src.plugin_system."):
        return (Status.FAIL, f"禁止 import 插件系统内部模块 {module}（只能用 ctx / 公共 API）")
    if module.startswith("src.plugins."):
        if self_pkg in parts:
            return None  # 插件自身子模块（绝对路径形式）
        return (Status.FAIL, f"禁止 import 其它插件 {module}")
    if module == "src" or module.startswith("src."):
        return (Status.FAIL, f"禁止 import 核心子系统 {module}（插件只能经 ctx / core.* 够到）")
    if top in sys.stdlib_module_names:
        return None  # 安全标准库
    # 第三方依赖提示供 AI 和维护者核对；直接运行依赖仍必须在 manifest 声明。
    return (Status.WARN, f"使用第三方依赖 {module}")


def _dangerous_call(node) -> Optional[str]:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALLS:
        return node.func.id
    return None
