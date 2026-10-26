"""下架插件的新版审核上下文；不改变市场可见性。"""
from pathlib import Path

from .availability import is_active
from .common import assert_binding, object_digest, read_json, version_key


def review_context(root, request):
    root = Path(root)
    entry = assert_binding(root, request, allow_withdrawn=True)
    if entry is None:
        return None
    if is_active(entry):
        # 已发布成功后的同一申请重试，保留审核时的绑定，便于补完通知。
        record_path = root / "versions" / request["plugin_name"] / (request["tag"][1:] + ".json")
        if record_path.is_file():
            record = read_json(record_path)
            if (record.get("status") == "approved" and record["validation"]["request"] == request
                    and entry.get("moderation", {}).get("action") == "relist"
                    and entry["moderation"].get("version") == request["tag"][1:]):
                return record["validation"].get("relisting")
        return None
    versions = [version_key(path.stem) for path in (root / "versions" / request["plugin_name"]).glob("*.json")]
    if versions and version_key(request["tag"][1:]) <= max(versions):
        raise ValueError("重新上架必须提交高于历史记录的新版本，不能覆盖原版本")
    return {"registry_sha256": object_digest(entry), "withdrawal": entry.get("moderation", {})}
