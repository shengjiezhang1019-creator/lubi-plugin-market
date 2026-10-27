"""本机一次授权，自动保存 Actions 密钥、公钥并触发启用；不输出秘密。"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from cryptography.exceptions import InvalidSignature

from .github import GitHub, GitHubAPIError
from .security import canonical
from .security_enable import CLIENT_KEYS


SECRET = "MARKET_SECURITY_PRIVATE_KEY"
VARIABLE = "MARKET_SECURITY_KEY_ID"


def credential_token():
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        return token
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                            capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "auto"})
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise ValueError("请先在电脑上登录 GitHub 管理员账号，再运行本命令；也可通过 GH_TOKEN 提供一次授权")
    return fields["password"]


def get_optional(api, path):
    try:
        return api.request(path)
    except GitHubAPIError as exc:
        if exc.status == 404:
            return None
        raise


def content(api, prefix, path, branch):
    from urllib.parse import quote
    value = get_optional(api, prefix + "/contents/" + path + "?ref=" + quote(branch, safe=""))
    if value is None:
        return None
    if value.get("encoding") != "base64":
        raise ValueError("仓库配置文件不是可读取的文本文件")
    return json.loads(base64.b64decode(value["content"]))


def backup_key(path, slug):
    path = Path(path)
    if path.exists():
        backup = json.loads(path.read_text(encoding="utf-8"))
        if backup.get("repository") != slug:
            raise ValueError("私钥备份属于其他市场，停止")
        private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(backup["private_key"], validate=True))
        expected = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        if backup.get("public_key") != expected or not backup.get("key_id"):
            raise ValueError("私钥备份内容不完整或不匹配")
        return backup
    key = Ed25519PrivateKey.generate()
    backup = {
        "repository": slug, "key_id": "market-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8],
        "private_key": base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode(),
        "public_key": base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(backup, stream)
    if os.name == "nt":
        account = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", account + ":F"], capture_output=True)
        if result.returncode:
            raise ValueError("私钥备份访问权限设置失败，尚未上传私钥")
    return backup


def publish_files(api, prefix, branch, files):
    from urllib.parse import quote
    ref_path = prefix + "/git/ref/heads/" + quote(branch, safe="")
    head = api.request(ref_path)["object"]["sha"]
    commit = api.request(prefix + "/git/commits/" + head)
    tree = api.request(prefix + "/git/trees", "POST", {
        "base_tree": commit["tree"]["sha"],
        "tree": [{"path": path, "mode": "100644", "type": "blob",
                  "content": json.dumps(value, indent=2, sort_keys=True) + "\n"} for path, value in files.items()],
    })
    new = api.request(prefix + "/git/commits", "POST", {
        "message": "Prepare public keys for automatic security notification setup", "tree": tree["sha"], "parents": [head],
    })
    # 默认分支发生并发更新时拒绝覆盖，保留本机备份，重新运行即可。
    api.request(prefix + "/git/refs/heads/" + quote(branch, safe=""), "PATCH", {"sha": new["sha"], "force": False})


def setup(api, slug, private_path):
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", slug) or slug.split("/")[1] in (".", ".."):
        raise ValueError("GitHub 仓库名不合法，应为 owner/repo")
    prefix = "/repos/" + slug
    repo = api.request(prefix)
    if not repo.get("permissions", {}).get("admin"):
        raise ValueError("首次启用需要 GitHub 仓库管理员授权")
    branch = repo["default_branch"]
    # 先确认启用入口已发布，避免配置一半才发现工作流不存在。
    api.request(prefix + "/actions/workflows/security-enable.yml")
    keys = content(api, prefix, "security_keys.json", branch)
    feed = content(api, prefix, "security.json", branch)
    secret = get_optional(api, prefix + "/actions/secrets/" + SECRET)
    variable = get_optional(api, prefix + "/actions/variables/" + VARIABLE)
    key_id = variable.get("value") if variable else None
    if keys is not None and (not isinstance(keys, dict) or not keys):
        raise ValueError("已发布公钥文件无效，需要管理员修复，工具不会替换它")
    if feed is not None:
        if not keys:
            raise ValueError("已有封禁名单但缺少公钥，停止")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[feed["key_id"]], validate=True)).verify(
            base64.b64decode(feed["signature"], validate=True), canonical(feed["payload"]))
    backup = None
    if not secret or not keys:
        if (secret or keys or feed) and not Path(private_path).is_file():
            raise ValueError("现有配置不完整且缺少本机私钥备份；不会重新生成或覆盖已有钥匙")
        backup = backup_key(private_path, slug)
        if key_id and key_id != backup["key_id"]:
            raise ValueError("备份编号与现有配置不一致，停止")
        key_id = backup["key_id"]
        if keys and keys.get(key_id) != backup["public_key"]:
            raise ValueError("备份与已发布公钥不匹配，停止")
        keys = keys or {key_id: backup["public_key"]}
    if not key_id:
        if feed:
            key_id = feed["key_id"]
        elif len(keys) == 1:
            key_id = next(iter(keys))
        else:
            raise ValueError("无法确定现有钥匙编号，需要管理员确认")
    if key_id not in keys:
        raise ValueError("当前编号不在已发布公钥中，停止")
    Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[key_id], validate=True))
    if not secret:
        from nacl.public import PublicKey, SealedBox
        if get_optional(api, prefix + "/actions/secrets/" + SECRET) is not None:
            raise ValueError("其他操作刚刚配置了私钥，请重新运行；本次不会覆盖它")
        server = api.request(prefix + "/actions/secrets/public-key")
        encrypted = SealedBox(PublicKey(base64.b64decode(server["key"]))).encrypt(backup["private_key"].encode())
        api.request(prefix + "/actions/secrets/" + SECRET, "PUT", {
            "key_id": server["key_id"], "encrypted_value": base64.b64encode(encrypted).decode(),
        })
    if not variable:
        api.request(prefix + "/actions/variables", "POST", {"name": VARIABLE, "value": key_id})
    changed = {}
    if content(api, prefix, "security_keys.json", branch) != keys:
        changed["security_keys.json"] = keys
    if content(api, prefix, CLIENT_KEYS, branch) != keys:
        changed[CLIENT_KEYS] = keys
    if changed:
        publish_files(api, prefix, branch, changed)
    previous = api.request(prefix + "/actions/workflows/security-enable.yml/runs?per_page=5")["workflow_runs"]
    previous_id = max((r["id"] for r in previous), default=0)
    api.request(prefix + "/actions/workflows/security-enable.yml/dispatches", "POST", {"ref": branch})
    return {"repository": slug, "key_id": key_id, "previous_run_id": previous_id}


def wait_for_enable(api, result, timeout=300):
    deadline = time.monotonic() + timeout
    path = "/repos/" + result["repository"] + "/actions/workflows/security-enable.yml/runs?per_page=5"
    run_id = None
    while time.monotonic() < deadline:
        runs = api.request(path)["workflow_runs"]
        fresh = [r for r in runs if r["id"] > result["previous_run_id"] and r["event"] == "workflow_dispatch"]
        if fresh and run_id is None:
            run_id = min(r["id"] for r in fresh)
        run = next((r for r in fresh if r["id"] == run_id), None)
        if run and run["status"] == "completed":
            if run["conclusion"] != "success":
                raise ValueError("启用未完成，请查看运行记录：" + run["html_url"])
            return run["html_url"]
        time.sleep(5)
    raise ValueError("仍在等待 GitHub 完成，请到 Actions 的“启用安全通知”查看结果；重新运行不会更换钥匙")


def main():
    parser = argparse.ArgumentParser(description="自动启用市场安全通知；使用本机已有的 GitHub 管理员登录")
    parser.add_argument("--repository", help="owner/repo；省略时从当前 Git 仓库的 origin 获取")
    parser.add_argument("--private-backup", type=Path, help="首次生成私钥的本机备份位置，不会上传到代码仓库")
    parser.add_argument("--no-wait", action="store_true", help="提交启用任务后立即返回")
    args = parser.parse_args()
    slug = args.repository
    if not slug:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout.strip()
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", remote)
        if not match:
            raise ValueError("无法识别市场仓库，请用 --repository owner/repo 指定")
        slug = match[1]
    private = args.private_backup or Path.home() / ".lubi-plugin-market/keys" / (slug.replace("/", "--") + ".json")
    api = GitHub(token=credential_token())
    result = setup(api, slug, private)
    print("GitHub 配置已检查，正在启用安全通知；已有钥匙和封禁记录不会重置。", flush=True)
    if not args.no_wait:
        print("安全通知已启用：" + wait_for_enable(api, result))


if __name__ == "__main__":
    try:
        main()
    except InvalidSignature:
        raise SystemExit("现有安全名单签名无效，停止；不会清空名单或更换钥匙") from None
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
