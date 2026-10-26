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
from .availability import is_active
from .common import object_digest, read_json, repository, validate_request, write_json
from .github import GitHub
from .feedback import authorized_issue, failure, record_failure, safe_set_status as set_status
from .index import approve, build_catalog
from .pipeline import validate
from .review_report import render_review

MARKER = "<!-- lubi-market-validation:"


def assert_prompt_unchanged(report, root):
    current = object_digest((root / "prompts/plugin-review.md").read_text(encoding="utf-8"))
    if report.get("prompt_sha256") != current:
        raise ValueError("AI 审核提示词已变化或旧报告缺少绑定，请先 /recheck 后重新审阅")


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
        assert_prompt_unchanged(ai, args.root)
        assert_prompt_unchanged(approved_ai, args.root)
        assert_release_unchanged(api, validation)
        changed = approve(args.root, validation, approved_ai, actor, number)
        if changed:
            subprocess.run(["git", "add", "registry.json", "catalog.json", "versions"], cwd=args.root, check=True)
            subprocess.run(["git", "commit", "-m", f"Approve {request['plugin_name']} {request['tag']}"], cwd=args.root, check=True)
            # push 失败不得报告成功或关闭申请；concurrency + 无强推防止覆盖并发变更。
            subprocess.run(["git", "push"], cwd=args.root, check=True)
        write_json(args.work / "publication.json", {"stage": "publish", "status": "已上架", "request": request})
        set_status(api, slug, number, "已上架")
        api.comment(slug, number, f"✅ 已上架：已收录 {request['plugin_name']} {request['tag']}，commit `{validation['commit']}`。")
        api.request(f"/repos/{slug}/issues/{number}", "PATCH", {"state": "closed"})
    else:
        report = render_review(validation, ai, request)
        # 原始完整 AI JSON 存放在机器人评论的独立机器可读标记；不作为 shell 或批准指令。
        import base64
        payload = base64.b64encode(json.dumps(ai, ensure_ascii=False, sort_keys=True).encode()).decode()
        report += "\n<!-- lubi-ai-base64:" + payload + " -->\n" + review_marker(validation, ai, request)
        api.comment(slug, number, "🔵 检查和 AI 报告已完成，等待维护者人工审核；尚未上架。\n\n" + report)
        set_status(api, slug, number, "待人工审核")


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
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write("eligible=false\n")
    issue, allowed = authorized_issue(api, slug, event)
    if not allowed:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write("mode=ignored\n")
        return
    command = event.get("comment", {}).get("body", "").strip()
    mode = "approve" if command == "/approve" else "reject" if command.startswith("/reject ") else "review"
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write("eligible=true\nmode=" + mode + "\n")
    set_status(api, slug, number, "检查中")
    request = issue_request(issue.get("body", ""))
    args.work.mkdir(parents=True, exist_ok=True)
    write_json(args.work / "request.json", request)
    if mode == "approve":
        previous_ai(api, slug, number, args.work)


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
        if not is_active(entry):
            continue
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
        existing = read_json(path)
        if (existing.get("status") != "rejected" or existing.get("request") != req
                or existing.get("issue_number") != int(number)):
            raise ValueError("该版本已有其他审核记录，不能用拒绝命令覆盖")
        reason = existing["reason"]
    else:
        reason = command[8:].strip()
        write_json(path, {"status": "rejected", "request": req, "actor": actor, "reason": reason,
                          "issue_number": int(number)})
        subprocess.run(["git", "add", "versions"], cwd=args.root, check=True)
        subprocess.run(["git", "commit", "-m", f"Reject {req['plugin_name']} {req['tag']}"], cwd=args.root, check=True)
        subprocess.run(["git", "push"], cwd=args.root, check=True)
    write_json(args.work / "publication.json", {"stage": "reject", "status": "已拒绝", "request": req})
    set_status(api, slug, number, "已拒绝")
    api.comment(slug, number, "已拒绝本版本：" + reason)
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
    try:
        run_action(args)
    except Exception as exc:
        if args.action in {"gate", "validate", "ai", "publish", "reject"}:
            record_failure(args.work, args.action, exc)
        raise


def run_action(args):
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
            assert_prompt_unchanged(previous, args.root)
            write_json(args.work / "ai-review.json", previous)
        else:
            result = review(args.work / validation["source_dir"], validation, args.root / "prompts/plugin-review.md")
            write_json(args.work / "ai-review.json", result)
        if read_json(args.work / "ai-review.json").get("status") != "completed":
            raise ValueError("AI 审核未完成，未覆盖全部送审文件")
    elif args.action == "publish":
        publish(args)
    elif args.action == "discover":
        discover(args)
    elif args.action == "catalog":
        build_catalog(args.root)
    elif args.action == "reject":
        reject(args)
    elif args.action == "failure":
        failure(args, GitHub())
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
            print(json.dumps({"status": result["status"], "fingerprint": result["fingerprint"], "note": "本地离线静态演示；未访问 GitHub、未调用 AI、未安装依赖或验证插件运行，不能用于批准"}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"市场任务失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
