"""从公共组件基类及本地继承关系识别展示类型；只解析 AST，不导入源码。"""

from __future__ import annotations

import ast
from pathlib import Path


_BASES = {
    f"{module}.{base}": kind
    for module in ("src.plugin_system", "src.plugin_system.base", "src.plugin_system.base.component_contexts")
    for base, kind in (("BaseCommand", "command"), ("BaseTool", "tool"), ("BaseEvent", "event"))
}
_PLUGIN_BASES = {
    f"{module}.BasePlugin"
    for module in (
        "src.plugin_system",
        "src.plugin_system.base",
        "src.plugin_system.base.base_plugin",
    )
}
_HELPER_BASES = {
    "abc.ABC", "typing.Protocol", "typing.TypedDict", "typing.NamedTuple",
    "enum.Enum", "enum.IntEnum", "enum.StrEnum",
}
_COMPONENT_BASE_NAMES = {"BaseCommand", "BaseTool", "BaseEvent"}


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def analyze_component_types(source: Path) -> dict:
    modules = {}
    trees = {}
    symbols = dict(_BASES)
    harmless = set(_PLUGIN_BASES) | _HELPER_BASES | {"object"}
    uncertain = set()
    classes = []
    for path in sorted(source.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source)
        parts = list(relative.with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        module = ".".join(parts)
        package = parts if is_package else parts[:-1]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except SyntaxError:
            uncertain.add(f"{relative} 语法错误，组件分析未完成")
            continue  # 基础语法检查负责拒绝，不能据此报类型检查成功。
        trees[module] = (relative, tree)
        aliases = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split(".")[0]] = (
                        item.name if item.asname else item.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                prefix = list(package)
                if node.level:
                    prefix = prefix[: max(0, len(prefix) - node.level + 1)]
                    target = ".".join((*prefix, *((node.module or "").split(".") if node.module else ())))
                else:
                    target = node.module or ""
                for item in node.names:
                    if item.name == "*":
                        uncertain.add(f"{relative}:{node.lineno} 星号导入")
                    else:
                        aliases[item.asname or item.name] = ".".join(filter(None, (target, item.name)))
        modules[module] = aliases
        for name, target in aliases.items():
            symbols[f"{module}.{name}"] = target
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append((module, relative, node))
            elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                value = _dotted(node.value)
                head, *tail = value.split(".")
                target = ".".join((aliases.get(head, f"{module}.{head}"), *tail))
                for name in node.targets:
                    if isinstance(name, ast.Name):
                        symbols[f"{module}.{name.id}"] = target
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "type" and len(n.args) >= 3
            for n in ast.walk(tree)
        ):
            uncertain.add(f"{relative} 动态类型构造")

    def resolve(name):
        seen = set()
        while name in symbols and name not in seen:
            seen.add(name)
            name = symbols[name]
        return name

    def base_name(module, expression):
        name = _dotted(expression)
        if not name:
            return None
        head, *tail = name.split(".")
        root = modules[module].get(head, head if head == "object" else f"{module}.{head}")
        return resolve(".".join((root, *tail)))

    # 跨文件别名、本地间接继承与 re-export 可能需要多轮传播。
    for _ in range(len(classes) + len(symbols) + 1):
        changed = False
        for module, _, node in classes:
            key = f"{module}.{node.name}"
            bases = [base_name(module, base) for base in node.bases]
            kinds = {base for base in bases if base in {"command", "tool", "event"}}
            if len(kinds) == 1 and symbols.get(key) != next(iter(kinds)):
                symbols[key] = next(iter(kinds))
                changed = True
            ordinary_local = not bases and node.name not in _COMPONENT_BASE_NAMES
            if (ordinary_local or (bases and all(base in harmless for base in bases))) and key not in harmless:
                harmless.add(key)
                changed = True
        if not changed:
            break

    observed = {resolve(f"{module}.{node.name}") for module, _, node in classes}
    observed &= {"command", "tool", "event"}
    for module, (relative, tree) in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = base_name(module, node.func)
            if callee not in {
                "src.plugin_system.base.mount_point_types.LifecycleBindingRequest",
                "src.plugin_system.base.command_types.ChatCommandBinding",
                "src.plugin_system.LifecycleBindingRequest",
                "src.plugin_system.ChatCommandBinding",
            }:
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"component", "command"}:
                    continue
                value = keyword.value
                kind = base_name(module, value.func) if isinstance(value, ast.Call) else None
                if kind not in {"command", "tool", "event"}:
                    uncertain.add(f"{relative}:{node.lineno} 绑定组件通过实例、工厂或动态表达式提供")
    for module, relative, node in classes:
        kind = resolve(f"{module}.{node.name}")
        if kind in {"command", "tool", "event"}:
            pass
        elif node.bases and any(base_name(module, base) not in harmless for base in node.bases):
            uncertain.add(f"{relative}:{node.lineno} 类 {node.name} 的继承关系未解析")
        if not observed:
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in {
                    "build_lifecycle_bindings",
                    "build_chat_bindings",
                }:
                    for ret in (n for n in ast.walk(method) if isinstance(n, ast.Return)):
                        if not (isinstance(ret.value, (ast.Tuple, ast.List)) and not ret.value.elts):
                            uncertain.add(f"{relative}:{method.lineno} 动态绑定内容未确认")

    # 这是静态分类线索，不保证这些类最终被运行时注册；动态路径交由人工复核。
    return {"types": sorted(observed), "status": "unknown" if uncertain else "detected",
            "warnings": sorted(uncertain)}
