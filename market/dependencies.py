from __future__ import annotations

import hashlib
import importlib.metadata
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .common import digest


def baseline_requirements(path):
    if not Path(path).is_file():
        raise ValueError("基础依赖清单不存在，请先配置服务器标准环境")
    entries = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.!+_-]+)", line)
        if not match:
            raise ValueError("基线必须是完整固定版本 package==version，不接受 URL 或安装参数")
        name, version = match.groups()
        name = canonicalize_name(name)
        if name in entries:
            raise ValueError("基线依赖重复")
        Version(version)
        entries[name] = version
    if not entries:
        raise ValueError("正式审核必须提供非空的服务器完整基础依赖基线")
    return entries


def resolve(manifest, baseline, profile):
    """仅下载 wheel 并解析元数据，不导入、构建或执行依赖代码。"""
    entries = baseline_requirements(baseline)
    requested = [f"{d['name']}{d['version']}" for d in manifest.get("python_dependencies", [])]
    temporary_root = Path.cwd() / ".work" / "temporary"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lubi-deps-", dir=temporary_root) as temp:
        root = Path(temp)
        requirements = root / "requirements.txt"
        requirements.write_text("\n".join([*(f"{name}=={version}" for name, version in entries.items()), *requested]) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--isolated", "download", "--only-binary=:all:",
             "--index-url", "https://pypi.org/simple", "--disable-pip-version-check", "--no-input",
             "-r", str(requirements), "-d", str(root / "wheels")],
            text=True, capture_output=True, timeout=1200,
        )
        if result.returncode:
            # 日志无任何 token，失败只保存有限长度，不输出任意请求凭据。
            raise ValueError("基础环境与插件依赖无法解析或缺少兼容 wheel：\n" + result.stderr[-6000:])
        packages = []
        for wheel in sorted((root / "wheels").glob("*.whl")):
            name, version, _, _ = parse_wheel_filename(wheel.name)
            hasher = hashlib.sha256()
            with wheel.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            packages.append({"name": canonicalize_name(name), "version": str(version), "filename": wheel.name, "sha256": hasher.hexdigest()})
        if len({p["name"] for p in packages}) != len(packages):
            raise ValueError("解析结果含同名不同版本依赖")
        lockfile = root / "resolved.txt"
        lockfile.write_text("\n".join(
            f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}" for p in packages
        ) + "\n", encoding="utf-8")
        target = root / "installed"
        installed = subprocess.run(
            [sys.executable, "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
             "--require-hashes", "--no-compile", "--disable-pip-version-check", "--no-input",
             "--find-links", str(root / "wheels"), "--target", str(target), "-r", str(lockfile)],
            capture_output=True, text=True, timeout=1200,
        )
        if installed.returncode:
            raise ValueError("审核 wheel 安装验证失败：\n" + installed.stderr[-6000:])
        # 只读取目标目录元数据，不加入 sys.path，不执行 .pth、插件或第三方库。
        actual = {canonicalize_name(d.metadata["Name"]): d.version
                  for d in importlib.metadata.distributions(path=[str(target)])}
        if actual != {p["name"]: p["version"] for p in packages}:
            raise ValueError("安装后的包元数据与固定依赖清单不一致")
        return {"profile": profile, "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                "baseline_sha256": digest(Path(baseline).read_bytes()), "packages": packages,
                "validation": "wheel-installation", "runtime_tested": False}
