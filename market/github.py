from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .common import repository


class GitHub:
    def __init__(self, token=None):
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")

    def request(self, path, method="GET", data=None):
        # 身份凭据只发送给 GitHub API，不发送给插件源码或下载地址。
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("GitHub API path 不合法")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "lubi-plugin-market/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None if data is None else json.dumps(data).encode()
        if body:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request("https://api.github.com" + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("GitHub 响应超限")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # 不输出请求 headers、token 或服务端任意错误正文。
            raise RuntimeError(f"GitHub API HTTP {exc.code}: {method} {path}") from None

    def pages(self, path):
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            items = self.request(f"{path}{separator}per_page=100&page={page}")
            yield from items
            if len(items) < 100:
                break
            page += 1

    def release(self, url, tag):
        slug = repository(url).split("github.com/")[1]
        # 无凭据查询，防止把私有插件仓库当作公开插件发布。
        public = GitHub(token="")
        release = public.request(f"/repos/{slug}/releases/tags/{urllib.parse.quote(tag, safe='')}")
        if release["draft"] or release["prerelease"] or release["tag_name"] != tag:
            raise ValueError("必须是对应标签的公开正式 Release")
        commit = public.request(f"/repos/{slug}/commits/{urllib.parse.quote(tag, safe='')}")["sha"]
        return release, commit

    def permission(self, slug, actor):
        result = self.request(f"/repos/{slug}/collaborators/{urllib.parse.quote(actor, safe='')}/permission")
        return result.get("permission") in {"write", "admin"}

    def comment(self, slug, number, text):
        # 不让模型评论成为审批命令，不额外 @ 通知用户。
        text = text.replace("@", "＠")
        # 只过滤独立命令行；不能破坏源码链接或机器可读 base64 内部的斜线。
        text = re.sub(
            r"(?m)^([ \t]*)(/approve|/ap|/reject|/recheck|/mai_review)(?=\s|$)",
            lambda match: match[1] + match[2].replace("/", "／"), text,
        )
        if len(text) > 60000:
            raise ValueError("审核评论超限，不截断批准绑定信息；请缩短报告后重试")
        return self.request(f"/repos/{slug}/issues/{int(number)}/comments", "POST", {"body": text})
