"""已授权的 Actions 一键启用：复用已有钥匙，绝不清空已有封禁。"""
import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .common import read_json, write_json
from .github import GitHub
from .publication import push_index
from .security import canonical, prepare, read_payload


CLIENT_KEYS = "client_config/plugin_market_security_keys.json"


def enable(root, *, private_key, key_id):
    root = Path(root)
    if not private_key or not key_id:
        raise ValueError("首次启用请先在电脑的市场项目目录运行 python -m market.security_setup，完成一次 GitHub 管理员授权")
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key, validate=True))
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    keys_path = root / "security_keys.json"
    if keys_path.exists():
        keys = read_json(keys_path)
        if keys.get(key_id) != public:
            raise ValueError("已发布公钥与现有签名私钥不匹配，停止；不会自动更换钥匙")
    else:
        if (root / "security.json").exists():
            raise ValueError("已有安全名单但缺少公钥，需要管理员恢复公钥")
        keys = {key_id: public}
        write_json(keys_path, keys)
    if (root / "security.json").exists():
        payload = read_payload(root)
        # 也验证当前私钥确实可签名，不能只看 Secret 是否存在。
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(private.sign(canonical(payload)), canonical(payload))
    else:
        envelope, _ = prepare(root, action="initialize", name="", version="", record_id="",
                              reason="首次启用安全通知", advice="", actor="market-setup",
                              private_key=private_key, key_id=key_id)
        write_json(root / "security.json", envelope)
        payload = envelope["payload"]
    # 安装包可直接包含此公开配置；不让客户端在线下载陌生公钥后自动信任。
    destination = root / CLIENT_KEYS
    if not destination.exists() or read_json(destination) != keys:
        write_json(destination, keys)
    return {"revision": payload["revision"], "records": len(payload["records"]), "key_id": key_id}


def run(root):
    import subprocess

    if os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("只允许管理员手动启用")
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    if os.getenv("GITHUB_REF") != "refs/heads/" + event["repository"]["default_branch"]:
        raise ValueError("请在默认分支启用")
    actor = os.getenv("GITHUB_TRIGGERING_ACTOR") or os.environ["GITHUB_ACTOR"]
    if not GitHub().permission(os.environ["GITHUB_REPOSITORY"], actor):
        raise ValueError("只有市场维护者可以启用安全通知")
    result = enable(root, private_key=os.getenv("MARKET_SECURITY_PRIVATE_KEY", ""),
                    key_id=os.getenv("MARKET_SECURITY_KEY_ID", ""))
    push_index(root, "Enable signed plugin security notifications",
               ["security_keys.json", "security.json", CLIENT_KEYS], subprocess.run)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("## 安全通知已启用\n\n已保留现有钥匙和封禁记录。\n\n"
                         f"通知序号：{result['revision']}；记录数：{result['records']}。\n\n"
                         f"客户端安装包可使用 `{CLIENT_KEYS}` 中的公钥配置。\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    run(parser.parse_args().root)
