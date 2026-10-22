"""市场仅记录依赖声明；实际环境解析与补装由露比运行时完成。"""
from __future__ import annotations


def declaration_report(manifest):
    return {"policy": "runtime-environment-v1", "declared": manifest.get("python_dependencies", []),
            "installation_tested": False, "runtime_tested": False}
