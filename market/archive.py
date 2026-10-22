from __future__ import annotations

import io
import stat
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .common import COMMIT, digest, repository

MAX_ARCHIVE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024


class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "codeload.github.com"}:
            raise ValueError("下载重定向到未允许的地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, commit):
    if not COMMIT.fullmatch(commit):
        raise ValueError("必须使用完整 commit")
    url = repository(url) + f"/archive/{commit}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "lubi-plugin-market/0.1"})
    with urllib.request.build_opener(DownloadRedirect()).open(request, timeout=60) as response:
        data = response.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise ValueError("源码 ZIP 超过 16 MiB")
    return data


def unpack(data, destination):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("源码 ZIP 超限")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    top = set()
    seen = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > 2000:
            raise ValueError("ZIP 文件数超限")
        for info in archive.infolist():
            # ZipInfo 在 Windows 会规范化 filename，校验必须使用 ZIP 中的原始名称。
            name = info.orig_filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or "\\" in name or any(
                part in {".", ".."} or ":" in part or part.rstrip(" .") != part
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                for part in parts
            )):
                raise ValueError("ZIP 含不安全路径")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("ZIP 不允许符号链接")
            normalized = "/".join(parts).casefold()
            if normalized in seen:
                raise ValueError("ZIP 含重复或大小写冲突路径")
            seen.add(normalized)
            top.add(parts[0])
            total += info.file_size
            if info.file_size > MAX_FILE or total > MAX_TOTAL or info.file_size / max(info.compress_size, 1) > 200:
                raise ValueError("ZIP 解压限额超出")
            if info.is_dir():
                continue
            if len(parts) < 2:
                raise ValueError("ZIP 必须有唯一顶层目录")
            # 审核保留归档内全部文件，包括隐藏目录；不能在送审前静默丢弃。
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    if len(top) != 1:
        raise ValueError("ZIP 必须有唯一顶层目录")
    return destination / next(iter(top)), digest(data)
