from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import os
import tempfile

from .availability import is_active
from .common import assert_binding, now, object_digest, read_json, repository, version_key, write_json
from .relisting import review_context


@contextmanager
def index_transaction(paths):
    """恢复写入前的精确字节，避免失败后残留半份登记、版本或目录。"""
    snapshots = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        yield
    except Exception:
        failures = []
        for path, original in snapshots.items():
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    fd, temporary = tempfile.mkstemp(prefix=".market-rollback-", dir=path.parent)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(original)
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            except OSError as exc:
                failures.append(exc)
        if failures:
            raise RuntimeError("市场索引写入失败且回滚未完整完成，需维护者核对文件后重试") from failures[0]
        raise


def build_catalog(root, *, write=True):
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
    if write:
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
    registered = assert_binding(root, request, allow_withdrawn=bool(validation.get("relisting")))
    withdrawn = registered is not None and not is_active(registered)
    if withdrawn:
        context = review_context(root, request)
        if validation.get("relisting") != context:
            raise ValueError("下架状态或原因已变化，请重新提交申请并审阅重新上架申请")
    elif validation.get("relisting") and review_context(root, request) != validation["relisting"]:
        raise ValueError("市场状态已变化，请重新提交申请并审阅")
    path = root / "versions" / request["plugin_name"] / (validation["manifest"]["version"] + ".json")
    if path.exists():
        old = read_json(path)
        if old["status"] == "approved" and old["validation"]["fingerprint"] == fingerprint:
            if registered is None:
                raise ValueError("已批准版本缺少插件登记，请维护者核对登记记录，不能跳过发布")
            expected = build_catalog(root, write=False)
            catalog_path = root / "catalog.json"
            try:
                current = read_json(catalog_path)
            except (OSError, ValueError):
                current = None
            if current != expected:
                with index_transaction([catalog_path]):
                    write_json(catalog_path, expected)
                return True  # CLI 必须提交修复后的目录，不能把修复误判为无需 push。
            return False
        raise ValueError("同版本已有审核记录，不能覆盖或替换源码")
    record = {"status": "approved", "actor": actor, "issue_number": int(issue_number), "validation": validation, "ai_review": ai}
    # CLI 负责权限和批准时重新验证。这里只写可信调用的结果；生产写入由 Actions concurrency 串行保护。
    registry = read_json(root / "registry.json")
    registry["plugins"].setdefault(request["plugin_name"], {"repository": request["repository"], "issue_number": int(issue_number)})
    if withdrawn:
        entry = registry["plugins"][request["plugin_name"]]
        operation = {"action": "relist", "actor": actor, "reason": "维护者批准修复版重新上架",
                     "at": now(), "version": validation["manifest"]["version"], "issue_number": int(issue_number)}
        entry["status"] = "active"
        entry["moderation"] = operation
        entry.setdefault("moderation_history", []).append(operation)
    with index_transaction([path, root / "registry.json", root / "catalog.json"]):
        write_json(path, record)
        write_json(root / "registry.json", registry)
        build_catalog(root)
    return True
