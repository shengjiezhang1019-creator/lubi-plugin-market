from __future__ import annotations

from pathlib import Path

from .availability import is_active
from .common import assert_binding, object_digest, read_json, repository, version_key, write_json


def build_catalog(root):
    root = Path(root)
    plugins = read_json(root / "registry.json")["plugins"]
    catalog = []
    for name, registered in sorted(plugins.items()):
        if not is_active(registered):
            continue
        records = []
        for path in (root / "versions" / name).glob("*.json"):
            record = read_json(path)
            if record["status"] != "approved":
                continue
            validation = record["validation"]
            if validation["request"]["plugin_name"] != name or repository(validation["request"]["repository"]) != registered["repository"]:
                raise ValueError("版本记录与登记绑定不一致")
            records.append(record)
        if not records:
            continue
        record = max(records, key=lambda r: version_key(r["validation"]["manifest"]["version"]))
        validation = record["validation"]
        catalog.append({"id": name, "repositoryUrl": registered["repository"], "commit": validation["commit"],
                        "sha256": validation["sha256"], "manifest": validation["manifest"],
                        "tags": validation["manifest"].get("tags", []), "comp_types": validation.get("component_analysis", {}).get("types", []),
                        "dependency_policy": validation.get("dependency_policy", {"policy": "runtime-environment-v1"}),
                        "release_notes": validation["release_notes"], "published_at": validation["published_at"]})
    write_json(root / "catalog.json", catalog)
    return catalog


def approve(root, validation, ai, actor, issue_number):
    root = Path(root)
    if validation.get("status") != "validated" or validation.get("dependency_status") != "runtime":
        raise ValueError("未完成正式静态与依赖声明检查，不能收录")
    canonical = dict(validation)
    fingerprint = canonical.pop("fingerprint")
    if fingerprint != object_digest(canonical):
        raise ValueError("验证报告已被修改")
    if ai.get("status") != "completed" or ai.get("commit") != validation["commit"] or ai.get("validation_fingerprint") != fingerprint:
        raise ValueError("缺少绑定当前内容的完整 AI 报告")
    request = validation["request"]
    assert_binding(root, request)
    path = root / "versions" / request["plugin_name"] / (validation["manifest"]["version"] + ".json")
    if path.exists():
        old = read_json(path)
        if old["status"] == "approved" and old["validation"]["fingerprint"] == fingerprint:
            return False
        raise ValueError("同版本已有审核记录，不能覆盖或替换源码")
    record = {"status": "approved", "actor": actor, "issue_number": int(issue_number), "validation": validation, "ai_review": ai}
    # CLI 负责权限和批准时重新验证。这里只写可信调用的结果；生产写入由 Actions concurrency 串行保护。
    write_json(path, record)
    registry = read_json(root / "registry.json")
    registry["plugins"].setdefault(request["plugin_name"], {"repository": request["repository"], "issue_number": int(issue_number)})
    write_json(root / "registry.json", registry)
    build_catalog(root)
    return True
