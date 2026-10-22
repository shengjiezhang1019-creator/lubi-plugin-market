from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .ai_review import review, source_bundle, validate_response
from .common import object_digest, read_json, repository, validate_request, version_key, write_json
from .github import GitHub
from .index import approve, build_catalog
from .pipeline import validate

MARKER = "<!-- lubi-market-validation:"


def issue_request(body):
    # GitHub Issue Forms 的固定标题；不使用 eval 或把正文拼进 shell。
    fields = {}
    for label, field in (("插件名称", "plugin_name"), ("GitHub 仓库", "repository"), ("Release 标签", "tag")):
        match = re.search(r"(?:^|\n)### " + label + r"\s*\n+([^\n]+)", body)
        if not match:
            raise ValueError(f"申请缺少 {label}")
        fields[field] = match[1].strip()
    return validate_request(fields)


def snapshot_issue(api, slug, number):
    issue = api.request(f"/repos/{slug}/issues/{int(number)}")
    if issue.get("pull_request") or issue["state"] != "open":
        raise ValueError("只处理开启中的 Issue，不处理 PR 或已关闭申请")
    return issue, issue_request(issue.get("body", ""))


def review_marker(validation, ai, request):
    return MARKER + json.dumps({"fingerprint": validation["fingerprint"], "request_sha256": object_digest(request),
        "ai_sha256": object_digest(ai)}, sort_keys=True) + " -->"


def trusted_marker(api, slug, number):
    markers = []
    for comment in api.pages(f"/repos/{slug}/issues/{int(number)}/comments"):
        if comment["user"].get("login") != "github-actions[bot]" or comment["user"].get("type") != "Bot":
            continue
        body = comment.get("body", "")
        match = re.search(re.escape(MARKER) + r"(\{[^\n]+\}) -->", body)
        if match:
            markers.append(json.loads(match[1]))
    if not markers:
        raise ValueError("缺少机器人发布的完整检查和 AI 审核报告，先执行 /recheck")
    return markers[-1]


def assert_release_unchanged(api, validation):
    request = validation["request"]
    release, commit = api.release(request["repository"], request["tag"])
    if commit != validation["commit"]:
        raise ValueError("审核后标签已移动，必须重新检查")
    from .archive import download
    if hashlib.sha256(download(request["repository"], commit)).hexdigest() != validation["sha256"]:
        raise ValueError("审核后的源码 ZIP 摘要已变化，必须重新检查")
    return release


def publish(args):
    api = GitHub()
    slug = os.environ["GITHUB_REPOSITORY"]
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    number = event["issue"]["number"]
    issue, request = snapshot_issue(api, slug, number)
    validation = read_json(args.work / "validation.json")
    ai = read_json(args.work / "ai-review.json")
    # Worker 状态和绑定均 fail closed。不能用 static-only 或 mock AI 作为上架结果。
    canonical = dict(validation)
    if canonical.pop("fingerprint") != object_digest(canonical) or request != validation["request"]:
        raise ValueError("申请或报告发生变化，需要重新检查")
    if validation["status"] != "validated" or ai["status"] != "completed" or ai["validation_fingerprint"] != validation["fingerprint"]:
        raise ValueError("检查或 AI 审核未完成")
    command = event.get("comment", {}).get("body", "").strip()
    actor = event.get("comment", {}).get("user", {}).get("login", "")
    if command == "/approve":
        if not api.permission(slug, actor):
            raise ValueError("只有市场仓库 write/admin 维护者可以批准")
        marker = trusted_marker(api, slug, number)
        if marker["fingerprint"] != validation["fingerprint"] or marker["request_sha256"] != object_digest(request):
            raise ValueError("批准内容与上一次已发布报告不一致，请重新检查并审阅后批准")
        # 复用维护者实际看到的 AI 报告，避免重复调用模型产生不同审核内容。
        approved_ai = read_json(args.work / "previous-ai.json")
        if object_digest(approved_ai) != marker["ai_sha256"]:
            raise ValueError("AI 报告不等于维护者已查看的报告")
        assert_release_unchanged(api, validation)
        changed = approve(args.root, validation, approved_ai, actor, number)
        if changed:
            subprocess.run(["git", "add", "registry.json", "catalog.json", "versions"], cwd=args.root, check=True)
            subprocess.run(["git", "commit", "-m", f"Approve {request['plugin_name']} {request['tag']}"], cwd=args.root, check=True)
            # push 失败不得报告成功或关闭申请；concurrency + 无强推防止覆盖并发变更。
            subprocess.run(["git", "push"], cwd=args.root, check=True)
        api.comment(slug, number, f"已收录 {request['plugin_name']} {request['tag']}，commit `{validation['commit']}`。")
        api.request(f"/repos/{slug}/issues/{number}", "PATCH", {"state": "closed"})
    else:
        report = "## 静态与依赖检查通过，等待维护者审核\n\n"
        report += f"插件：{request['plugin_name']}，版本：{request['tag']}\n\ncommit：`{validation['commit']}`\n\n"
        report += "AI 报告仅供参考，不自动上架。\n\n```json\n" + json.dumps(ai, ensure_ascii=False, indent=2) + "\n```\n"
        # 原始完整 AI JSON 存放在机器人评论的独立机器可读标记；不作为 shell 或批准指令。
        import base64
        payload = base64.b64encode(json.dumps(ai, ensure_ascii=False, sort_keys=True).encode()).decode()
        report += "\n<!-- lubi-ai-base64:" + payload + " -->\n" + review_marker(validation, ai, request)
        api.comment(slug, number, report)


def previous_ai(api, slug, number, work):
    import base64
    matches = []
    for comment in api.pages(f"/repos/{slug}/issues/{int(number)}/comments"):
        if comment["user"].get("login") != "github-actions[bot]" or comment["user"].get("type") != "Bot":
            continue
        body = comment.get("body", "")
        if MARKER not in body:
            continue
        encoded = re.search(r"<!-- lubi-ai-base64:([A-Za-z0-9+/=]+) -->", body)
        if encoded:
            matches.append(json.loads(base64.b64decode(encoded[1])))
    if not matches:
        raise ValueError("没有可复核的 AI 审核报告")
    write_json(work / "previous-ai.json", matches[-1])
    return matches[-1]


def gate(args):
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    slug = os.environ["GITHUB_REPOSITORY"]
    api = GitHub()
    number = event["issue"]["number"]
    issue, request = snapshot_issue(api, slug, number)
    command = event.get("comment", {}).get("body", "").strip()
    actor = event.get("comment", {}).get("user", {}).get("login", "")
    allowed = True
    if "comment" in event:
        if command == "/approve" or command.startswith("/reject "):
            allowed = api.permission(slug, actor)
        elif command == "/recheck":
            allowed = actor == issue["user"]["login"] or api.permission(slug, actor)
        else:
            allowed = False
    if not allowed:
        raise ValueError("命令不受支持或无权限")
    args.work.mkdir(parents=True, exist_ok=True)
    write_json(args.work / "request.json", request)
    if command == "/approve":
        previous_ai(api, slug, number, args.work)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write("mode=" + ("approve" if command == "/approve" else "reject" if command.startswith("/reject ") else "review") + "\n")


def discover(args):
    api = GitHub()
    slug = os.environ["GITHUB_REPOSITORY"]
    existing = set()
    for issue in api.pages(f"/repos/{slug}/issues?state=all"):
        if issue.get("pull_request"):
            continue
        try:
            item = issue_request(issue.get("body", ""))
        except ValueError:
            continue
        existing.add((item["plugin_name"], item["repository"], item["tag"]))
    errors = []
    for name, entry in read_json(args.root / "registry.json")["plugins"].items():
        try:
            url = repository(entry["repository"])
            repo = url.split("github.com/")[1]
            releases = list(GitHub(token="").pages(f"/repos/{repo}/releases"))
            for release in releases:
                if release["draft"] or release["prerelease"]:
                    continue
                try:
                    req = validate_request({"plugin_name": name, "repository": url, "tag": release["tag_name"]})
                except ValueError:
                    continue
                key = (name, url, req["tag"])
                if key in existing or (args.root / "versions" / name / (req["tag"][1:] + ".json")).exists():
                    continue
                body = f"### 插件名称\n\n{name}\n\n### GitHub 仓库\n\n{url}\n\n### Release 标签\n\n{req['tag']}\n\n市场自动发现的新版本，等待检查与人工批准。"
                api.request(f"/repos/{slug}/issues", "POST", {"title": f"[Release] {name} {req['tag']}", "body": body})
                existing.add(key)
        except Exception as exc:
            errors.append({"plugin": name, "error": str(exc)})
    write_json(args.work / "discovery.json", {"errors": errors})
    if errors:
        raise ValueError("部分仓库扫描失败，旧索引保持不变，见 discovery.json")


def reject(args):
    api = GitHub()
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    slug = os.environ["GITHUB_REPOSITORY"]
    actor = event["comment"]["user"]["login"]
    command = event["comment"]["body"].strip()
    if not command.startswith("/reject ") or not command[8:].strip() or not api.permission(slug, actor):
        raise ValueError("拒绝命令缺少原因或无权限")
    number = event["issue"]["number"]
    issue, req = snapshot_issue(api, slug, number)
    path = args.root / "versions" / req["plugin_name"] / (req["tag"][1:] + ".json")
    if path.exists():
        raise ValueError("该版本已有记录，不能用拒绝命令覆盖")
    write_json(path, {"status": "rejected", "request": req, "actor": actor, "reason": command[8:].strip()})
    subprocess.run(["git", "add", "versions"], cwd=args.root, check=True)
    subprocess.run(["git", "commit", "-m", f"Reject {req['plugin_name']} {req['tag']}"], cwd=args.root, check=True)
    subprocess.run(["git", "push"], cwd=args.root, check=True)
    api.comment(slug, number, "已拒绝本版本：" + command[8:].strip())
    api.request(f"/repos/{slug}/issues/{number}", "PATCH", {"state": "closed"})


def main():
    parser = argparse.ArgumentParser(description="露比插件市场工具")
    parser.add_argument("action", choices=["gate", "validate", "ai", "publish", "discover", "catalog", "reject", "demo", "failure"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--work", type=Path, default=Path(".work/current"))
    parser.add_argument("--request", type=Path)
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.action == "gate":
        gate(args)
    elif args.action == "validate":
        validate(args.root, read_json(args.request or args.work / "request.json"), args.work, static_only=args.static_only)
    elif args.action == "ai":
        validation = read_json(args.work / "validation.json")
        if (args.work / "previous-ai.json").exists():
            # 批准时同样复核全部文件，旧报告不能绕过当前的完整覆盖要求。
            files = source_bundle(args.work / validation["source_dir"])
            previous = validate_response(read_json(args.work / "previous-ai.json"), validation["commit"],
                                         {file["path"] for file in files})
            write_json(args.work / "ai-review.json", previous)
        else:
            result = review(args.work / validation["source_dir"], validation, args.root / "prompts/plugin-review.md")
            write_json(args.work / "ai-review.json", result)
    elif args.action == "publish":
        publish(args)
    elif args.action == "discover":
        discover(args)
    elif args.action == "catalog":
        build_catalog(args.root)
    elif args.action == "reject":
        reject(args)
    elif args.action == "failure":
        event = read_json(os.environ["GITHUB_EVENT_PATH"])
        slug = os.environ["GITHUB_REPOSITORY"]
        run_id = os.environ["GITHUB_RUN_ID"]
        GitHub().comment(slug, event["issue"]["number"],
            "本次市场任务未完成，未确认上架成功。请查看工作流日志并修正配置或申请后重试：\n\n"
            + f"https://github.com/{slug}/actions/runs/{run_id}")
    elif args.action == "demo":
        # 临时元数据保持正式 registry/catalog 不变；规则直接使用本仓库版本。
        with tempfile.TemporaryDirectory(prefix="demo-meta-", dir=args.work) as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            config = read_json(args.root / "config/market.json")
            write_json(root / "config/market.json", config)
            write_json(root / "registry.json", {"schema_version": 1, "plugins": {}})
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for file in (args.root / "examples/demo_plugin").rglob("*"):
                    if file.is_file():
                        archive.write(file, "demo/" + file.relative_to(args.root / "examples/demo_plugin").as_posix())
            req = {"plugin_name": "demo_plugin", "repository": "https://github.com/example/demo-plugin", "tag": "v0.1.0"}
            result, source = validate(root, req, args.work, static_only=True, archive_data=buffer.getvalue(), commit="a" * 40)
            print(json.dumps({"status": result["status"], "fingerprint": result["fingerprint"], "note": "本地离线静态演示；未访问 GitHub、未调用 AI、未解析生产依赖，不能用于批准"}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"市场任务失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
