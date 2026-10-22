from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from .common import object_digest


def source_bundle(source, max_bytes=400000):
    source = Path(source)
    files = []
    size = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"审核文件不允许符号链接: {path.relative_to(source).as_posix()}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("审核包包含无法读取的非普通文件")
        relative = path.relative_to(source).as_posix()
        raw = path.read_bytes()
        size += len(raw)
        if size > max_bytes:
            raise ValueError("源码超过 AI 直接审核额度，需人工处理；不截断后宣称完整审核")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"无法完整审核文件 {relative}：非 UTF-8 文本；不跳过后宣称审核完成") from None
        if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            raise ValueError(f"无法完整审核文件 {relative}：含二进制控制字符；不跳过后宣称审核完成")
        files.append({"path": relative, "content": text})
    return files


def validate_response(report, commit, paths):
    if not isinstance(report, dict) or report.get("commit") != commit:
        raise ValueError("AI 报告 commit 不匹配")
    if report.get("status") not in {"completed", "incomplete"}:
        raise ValueError("AI 报告缺少明确完成状态")
    for field in ("summary", "limitations"):
        if not isinstance(report.get(field), str) or not report[field].strip():
            raise ValueError(f"AI 报告缺少 {field}")
    files_read = report.get("files_read")
    if (not isinstance(files_read, list) or not all(isinstance(path, str) for path in files_read)
            or len(set(files_read)) != len(files_read) or not set(files_read) <= paths):
        raise ValueError("AI 未提供有效文件阅读范围，或含重复、不存在的文件")
    missing = sorted(paths - set(files_read))
    report = dict(report)
    if missing:
        report["status"] = "incomplete"
        report["limitations"] += "；尚未确认阅读全部文件：" + "、".join(missing)
    if not files_read and report["status"] == "completed":
        raise ValueError("AI 未提供有效文件阅读范围")
    if not isinstance(report.get("findings"), list):
        raise ValueError("AI 缺少风险列表")
    for finding in report["findings"]:
        if not isinstance(finding, dict) or finding.get("severity") not in {"high", "medium", "low", "question"}:
            raise ValueError("AI 风险项格式不合法")
        if finding.get("file") not in paths or not isinstance(finding.get("line"), int) or finding["line"] < 1:
            raise ValueError("AI 引用了不存在的源码位置")
        if not all(isinstance(finding.get(k), str) and finding[k].strip() for k in ("evidence", "impact", "suggestion")):
            raise ValueError("AI 风险项缺少证据、影响或建议")
    return report


def review(source, validation, prompt_path, max_bytes=400000):
    base = os.environ.get("REVIEW_API_BASE", "").rstrip("/")
    model = os.environ.get("REVIEW_MODEL", "")
    key = os.environ.get("REVIEW_API_KEY", "")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise ValueError("需配置可信的 HTTPS REVIEW_API_BASE")
    if not model or not key:
        raise ValueError("AI 审核未配置 REVIEW_MODEL / REVIEW_API_KEY；不能生成通过报告")
    files = source_bundle(source, max_bytes)
    payload = {"model": model, "messages": [
        {"role": "system", "content": Path(prompt_path).read_text(encoding="utf-8")},
        {"role": "user", "content": json.dumps({"validation": validation, "untrusted_source_files": files}, ensure_ascii=False)},
    ], "response_format": {"type": "json_object"}}
    request = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    # 不允许模型服务重定向转发 Bearer 凭据。
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=240) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("AI 响应超限")
    content = json.loads(raw)["choices"][0]["message"]["content"]
    report = validate_response(json.loads(content), validation["commit"], {f["path"] for f in files})
    lengths = {f["path"]: len(f["content"].splitlines()) for f in files}
    for finding in report["findings"]:
        if finding["line"] > lengths[finding["file"]]:
            raise ValueError("AI 引用了源码范围外的行号")
    report.update({"model": model, "validation_fingerprint": validation["fingerprint"],
                   "prompt_sha256": object_digest(Path(prompt_path).read_text(encoding="utf-8"))})
    return report
