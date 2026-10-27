"""离线生成市场签名密钥。私钥文件不得提交 Git 或发送给客户端。"""
import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption


def generate(private_path, public_path, key_id):
    private_path, public_path = Path(private_path).resolve(), Path(public_path).resolve()
    if private_path == public_path or private_path.exists() or public_path.exists():
        raise ValueError("输出文件已存在或路径重复，拒绝覆盖")
    if not key_id or len(key_id) > 64:
        raise ValueError("公钥编号不能为空且不超过 64 字符")
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(private_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(private)
    with public_path.open("x", encoding="utf-8") as stream:
        json.dump({key_id: public}, stream, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-output", required=True)
    parser.add_argument("--public-output", required=True)
    parser.add_argument("--key-id", required=True)
    args = parser.parse_args()
    generate(args.private_output, args.public_output, args.key_id)
