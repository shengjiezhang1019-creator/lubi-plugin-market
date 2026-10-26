from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile

from .availability import is_active
from datetime import datetime, timezone
from pathlib import Path

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REPO = re.compile(r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)/?$")


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".market-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_digest(data):
    return digest(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def repository(value):
    match = REPO.fullmatch(str(value).strip())
    if not match or match[2] in {".", ".."}:
        raise ValueError("仅支持公开 GitHub HTTPS 仓库根地址")
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if not repo or repo.endswith("."):
        raise ValueError("仓库名称不合法")
    return f"https://github.com/{owner.lower()}/{repo.lower()}"


def validate_request(request):
    if set(request) != {"plugin_name", "repository", "tag"}:
        raise ValueError("申请必须且只能包含 plugin_name、repository、tag")
    if not NAME.fullmatch(request["plugin_name"]):
        raise ValueError("插件名不合法")
    version = str(request["tag"]).removeprefix("v")
    if request["tag"] != f"v{version}" or not VERSION.fullmatch(version):
        raise ValueError("正式标签必须为 vX.Y.Z，不支持预发布")
    return {**request, "repository": repository(request["repository"])}


def assert_binding(root, request, *, allow_withdrawn=False):
    plugins = read_json(Path(root) / "registry.json")["plugins"]
    for name, entry in plugins.items():
        if name.casefold() == request["plugin_name"].casefold():
            if name != request["plugin_name"] or repository(entry["repository"]) != request["repository"]:
                raise ValueError("插件名称已绑定其他仓库，迁移必须走修改登记流程")
            if not is_active(entry) and not allow_withdrawn:
                raise ValueError("插件已下架，修复版须先重新提交申请并由维护者审阅报告后使用 /approve 批准重新上架")
            return entry


def version_key(value):
    if not VERSION.fullmatch(value):
        raise ValueError("版本必须为 X.Y.Z")
    return tuple(map(int, value.split(".")))
