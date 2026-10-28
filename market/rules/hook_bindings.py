"""静态观察 Hook 绑定声明并核对申报；不导入或执行插件，不证明运行时挂载。"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def parse_declared_hooks(body):
    section = re.search(r"(?:^|\n)### 使用的 Hook[^\S\n]*\n(.*?)(?=\n### |\Z)", body, re.S)
    if section is None:
        return None  # 兼容旧申请与无 Issue 的 CLI 检查。
    return sorted(set(re.findall(r"^\s*- \[[xX]\]\s+`?([A-Z][A-Z0-9_]*)\b", section[1], re.M)))


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def analyze_hook_bindings(source, declared=None):
    observed, evidence, warnings = set(), [], set()
    for path in sorted(Path(source).rglob("*.py")):
        relative = path.relative_to(source).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeError):
            warnings.add(f"{relative} 无法解析")
            continue
        symbols = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and not node.level:
                for alias in node.names:
                    symbols[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                    if alias.name == "*":
                        warnings.add(f"{relative}:{node.lineno} 星号导入无法确认")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )

        def resolve(node):
            name = _dotted(node)
            head, *tail = name.split(".")
            return ".".join((symbols.get(head, head), *tail))

        def point(node, constants):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name) and node.id in constants:
                return constants[node.id]
            name = resolve(node)
            prefix = "src.plugin_system.base.hook_contracts."
            if name.startswith(prefix) and name.endswith(".point_id"):
                parts = name[len(prefix) :].split(".")
                if len(parts) != 3:
                    return None
                module, definition, _ = parts
                expected = module.upper()
                if definition in {expected, expected + "_DEFINITION"}:
                    return expected
            return None

        constants = {}
        # 仅解析模块级静态常量；局部变量、跨模块别名与工厂保留为未知。
        for node in tree.body:
            if isinstance(node, ast.Assign):
                value = point(node.value, constants)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if value is not None:
                            constants[target.id] = value
                        else:
                            constants.pop(target.id, None)
        binding_names = {
            "src.plugin_system.base.mount_point_types.LifecycleBindingRequest",
            "src.plugin_system.LifecycleBindingRequest",
        }
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and resolve(node.func) in binding_names:
                calls.append(node)
                value = next((kw.value for kw in node.keywords if kw.arg == "point_id"), None)
                value = value if value is not None else (node.args[0] if node.args else None)
                hook = point(value, constants)
                if hook is None:
                    warnings.add(f"{relative}:{node.lineno} 动态 point_id 无法确认")
                else:
                    observed.add(hook)
                    evidence.append({"hook": hook, "file": relative, "line": node.lineno})
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_lifecycle_bindings":
                returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
                if (
                    node.decorator_list
                    or len(node.body) != 1
                    or len(returns) != 1
                    or not isinstance(returns[0].value, ast.Tuple)
                    or any(item not in calls for item in returns[0].value.elts)
                ):
                    warnings.add(f"{relative}:{node.lineno} 绑定由变量、工厂或控制流生成，需人工核实")
    declared_set = set(declared or [])
    missing = sorted(declared_set - observed) if declared is not None else []
    undeclared = sorted(observed - declared_set) if declared is not None else []
    # 结果只说明静态观察到什么；即使集合相同也不声称已验证真实挂载。
    return {
        "status": "needs_review" if warnings or missing or undeclared or declared is None else "consistent",
        "declared": declared,
        "observed": sorted(observed),
        "declared_not_observed": missing,
        "observed_not_declared": undeclared,
        "evidence": evidence,
        "warnings": sorted(warnings),
    }
