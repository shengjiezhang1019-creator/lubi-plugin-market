"""版本级安全封禁与解除封禁；只由维护者工作流发布。"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .common import now, read_json, write_json
from .github import GitHub


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_payload(root):
    root = Path(root)
    if not (root / "security.json").exists():
        return {"revision": 0, "records": []}
    envelope = read_json(root / "security.json")
    keys = read_json(root / "security_keys.json")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[envelope["key_id"]], validate=True))
    key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(envelope["payload"]))
    return envelope["payload"]


def is_blocked(root, name, repository, commit):
    return any(
        r["status"] == "blocked" and r["plugin_name"] == name and
        r["repository"].rstrip("/").lower() == repository.rstrip("/").lower() and r["commit"] == commit
        for r in read_payload(root)["records"]
    )


def prepare(root, *, action, name, version, record_id, reason, advice, actor, private_key, key_id):
    root = Path(root)
    if action not in ("ban", "unban", "initialize"):
        raise ValueError("仅支持安全封禁或解除封禁")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise ValueError("原因必填，最多 1000 字符")
    if not isinstance(advice, str) or len(advice) > 2000:
        raise ValueError("处理建议最多 2000 字符")
    if not actor:
        raise ValueError("缺少操作人")
    payload = copy.deepcopy(read_payload(root))
    operation = {"actor": actor, "reason": reason.strip(), "at": now(), "action": action}
    if action == "ban":
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name or ""):
            raise ValueError("插件名不合法")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version or ""):
            raise ValueError("填写完整版本号，如 1.2.0")
        registered = read_json(root / "registry.json")["plugins"].get(name)
        if not registered:
            raise ValueError("插件未登记")
        reviewed = read_json(root / "versions" / name / (version + ".json"))
        if reviewed["status"] != "approved":
            raise ValueError("版本未经批准")
        validation = reviewed["validation"]
        repository = registered["repository"].rstrip("/")
        if validation["request"]["repository"].rstrip("/").lower() != repository.lower():
            raise ValueError("审核记录仓库不匹配")
        commit = validation["commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("审核提交无效")
        if any(r["plugin_name"] == name and r["commit"] == commit and r["status"] == "blocked" for r in payload["records"]):
            raise ValueError("该版本已经封禁")
        record_id = "ban-" + uuid.uuid4().hex
        payload["records"].append({
            "id": record_id, "plugin_name": name, "repository": repository,
            "version": version, "commit": commit, "status": "blocked", "reason": reason.strip(),
            "advice": advice, "history": [operation],
        })
    elif action == "unban":
        record = next((r for r in payload["records"] if r["id"] == record_id), None)
        if not record or record["status"] != "blocked":
            raise ValueError("有效封禁记录不存在")
        record["status"] = "released"
        record["history"].append(operation)
    elif payload["revision"] != 0:
        raise ValueError("安全通知已初始化，无需重复操作")
    payload["revision"] += 1
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key, validate=True))
    keys = read_json(root / "security_keys.json")
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[key_id], validate=True))
    signature = key.sign(canonical(payload))
    public.verify(signature, canonical(payload))
    return {"key_id": key_id, "payload": payload, "signature": base64.b64encode(signature).decode("ascii")}, record_id


def run(root):
    root = Path(root)
    if os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("只允许维护者手动运行")
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    if os.getenv("GITHUB_REF") != "refs/heads/" + event["repository"]["default_branch"]:
        raise ValueError("只允许在默认分支运行")
    actor = os.getenv("GITHUB_TRIGGERING_ACTOR") or os.environ["GITHUB_ACTOR"]
    if not GitHub().permission(os.environ["GITHUB_REPOSITORY"], actor):
        raise ValueError("只有 write/admin 维护者可以封禁或解封")
    inputs = event["inputs"]
    envelope, record_id = prepare(
        root, action=inputs["action"], name=inputs.get("plugin_name", ""), version=inputs.get("version", ""),
        record_id=inputs.get("record_id", ""), reason=inputs["reason"], advice=inputs.get("advice", ""),
        actor=actor, private_key=os.environ["MARKET_SECURITY_PRIVATE_KEY"], key_id=os.environ["MARKET_SECURITY_KEY_ID"],
    )
    old_security = (root / "security.json").read_bytes() if (root / "security.json").exists() else None
    old_catalog = (root / "catalog.json").read_bytes()
    try:
        write_json(root / "security.json", envelope)
        from .index import build_catalog
        build_catalog(root)
    except Exception:
        if old_security is None:
            (root / "security.json").unlink(missing_ok=True)
        else:
            (root / "security.json").write_bytes(old_security)
        (root / "catalog.json").write_bytes(old_catalog)
        raise
    subprocess.run(["git", "add", "security.json", "catalog.json"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", f"Plugin security {inputs['action']}: {record_id}"], cwd=root, check=True)
    subprocess.run(["git", "push"], cwd=root, check=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        # 不在 workflow 摘要中插入未经转义的用户输入。
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"安全通知和市场目录已发布。封禁记录编号：`{record_id}`。\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    run(parser.parse_args().root)
