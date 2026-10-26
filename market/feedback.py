"""申请状态和面向作者的反馈；不把原始异常或凭据发布到评论。"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import quote

from .common import read_json, write_json
from .github import GitHubAPIError
from .requests import ExistingVersionError

LABELS = {
    "检查中": "fbca04", "检查失败": "d73a4a", "待人工审核": "0075ca",
    "已上架": "0e8a16", "已拒绝": "6f42c1",
}
STAGES = {"gate": "申请检查", "validate": "源码静态检查", "ai": "AI 审核",
          "publish": "审核报告或索引发布", "reject": "拒绝结果发布"}


def authorized_issue(api, slug, event):
    """先验证身份，再解析申请，防止无权限命令污染正常申请的状态。"""
    issue = api.request(f"/repos/{slug}/issues/{int(event['issue']['number'])}")
    if issue.get("pull_request") or issue.get("state") != "open":
        return issue, False
    if "comment" not in event:
        return issue, event.get("action") in {"opened", "edited"}
    command = event["comment"].get("body", "").strip()
    actor = event["comment"].get("user", {}).get("login", "")
    if command == "/recheck" and actor == issue.get("user", {}).get("login"):
        return issue, True
    if command in {"/recheck", "/approve"} or command.startswith("/reject "):
        return issue, api.permission(slug, actor)
    return issue, False


def set_status(api, slug, number, status):
    if status not in LABELS:
        raise ValueError("未知申请状态")
    # 只创建需要的标签，只移除本流程的互斥状态，保留作者和维护者的其他标签。
    encoded = quote(status, safe="")
    try:
        api.request(f"/repos/{slug}/labels/{encoded}")
    except GitHubAPIError as exc:
        if exc.status != 404:
            raise
        try:
            api.request(f"/repos/{slug}/labels", "POST", {"name": status, "color": LABELS[status]})
        except GitHubAPIError as duplicate:
            if duplicate.status != 422:
                raise
            api.request(f"/repos/{slug}/labels/{encoded}")  # 并发创建时确认标签确实存在。
    issue = api.request(f"/repos/{slug}/issues/{int(number)}")
    current = {label['name'] for label in issue.get("labels", [])}
    api.request(f"/repos/{slug}/issues/{int(number)}/labels", "POST", {"labels": [status]})
    for old in sorted((current & LABELS.keys()) - {status}):
        try:
            api.request(f"/repos/{slug}/issues/{int(number)}/labels/{quote(old, safe='')}", "DELETE")
        except GitHubAPIError as exc:
            if exc.status != 404:
                raise


def safe_set_status(api, slug, number, status):
    """标签属于辅助展示，失败不能阻止结果评论或补完已发布的申请。"""
    try:
        set_status(api, slug, number, status)
        return True
    except Exception:
        print("申请标签更新失败，继续发送结果反馈；请维护者检查标签接口。", file=sys.stderr)
        return False


def failure_detail(stage, exc):
    """只返回可信模板，异常正文中可能包含源码、服务响应或密钥，绝不直接拼接。"""
    message = str(exc)
    if isinstance(exc, ExistingVersionError):
        status = {"approved": "已批准", "rejected": "已拒绝"}.get(exc.status, "已有处理")
        original = f"请查看原申请 #{exc.issue_number}。" if exc.issue_number else "请维护者查看该版本的历史记录。"
        return f"该版本{status}，不会重复送交 AI 审核。", original + "如需修改源码，请发布新版本后申请，不要对原版本反复 /recheck。"
    retry = "无需修改插件。请稍后在本申请下评论 `/recheck`；仍失败时由市场维护者查看日志。"
    if stage == "gate" and isinstance(exc, ValueError):
        for field in ("插件名称", "GitHub 仓库", "Release 标签"):
            if message == "申请缺少 " + field:
                return f"申请未填写“{field}”。", "请编辑原申请补齐该项，保存后会重新检查，无需重复创建申请。"
        if message == "插件名不合法":
            return "插件名称格式不正确。", "请使用字母开头、仅含字母数字下划线或连字符的插件名，最多 64 字符。编辑原申请后重新检查。"
        if message.startswith("正式标签必须为"):
            return "Release 标签格式不正确。", "请填写已公开发布的正式 Release 标签，例如 v1.0.0；编辑原申请后重新检查。"
        if message in {"仅支持公开 GitHub HTTPS 仓库根地址", "仓库名称不合法"}:
            return "GitHub 仓库地址格式不正确。", "请填写公开仓库根地址，例如 https://github.com/作者/仓库；编辑原申请后重新检查。"
        return "缺少可复核的审核结果，或申请内容需要重新检查。", "请先评论 `/recheck` 生成完整报告，再由维护者审阅并批准。"
    if stage == "validate" and isinstance(exc, ValueError):
        if message.startswith("重新上架必须提交"):
            return "下架插件需要提交高于历史记录的新版本。", "请发布新的正式版本并更新原申请的 Release 标签；不要覆盖或移动历史版本。"
        for name in ("README.md", "plugin.py", "_manifest.json"):
            if message == "缺少必需文件 " + name:
                return f"发布源码的根目录缺少 {name}。", "请补齐文件并发布新的正式版本，然后编辑本申请的 Release 标签重新检查。"
        if message.startswith("manifest 不符合市场规则"):
            required = re.findall(r"'([a-z_]+)' is a required property", message)
            fields = {"name", "version", "description", "author", "license", "plugin_dependencies",
                      "python_dependencies"}
            missing = sorted(set(required) & fields)
            if missing:
                return ("_manifest.json 缺少必填字段：" + "、".join(missing) + "。",
                        "请补齐字段并发布新版本，然后编辑本申请的 Release 标签重新检查。")
            return "_manifest.json 不符合市场字段规范。", "请按市场规范核对必填字段和字段类型，修正后发布新版并更新本申请的 Release 标签。详细字段见日志。"
        if message.startswith("框架静态检查失败"):
            return "源码未通过框架静态检查。", "请查看日志中的检查项、文件位置和处理建议，修正后发布新版并更新本申请的 Release 标签。"
        return "发布版本、插件信息、源码包或依赖声明未通过检查。", "请查看日志确认失败项；若需修改源码，请发布新版并更新原申请的 Release 标签。"
    if stage == "ai":
        if isinstance(exc, ValueError) and (message.startswith("无法完整审核文件") or message.startswith("源码超过")):
            return "源码包无法完整送交 AI 审核。", "请由维护者查看文件格式或大小限制；不能跳过文件后宣称审核通过。"
        if isinstance(exc, ValueError) and message.startswith("AI ") and "未配置" not in message:
            return "AI 返回的报告未满足完整性校验，尚未完成审核。", retry
        return "AI 审核服务暂时不可用或配置异常，尚未完成审核。", retry
    if stage == "publish":
        if isinstance(exc, ValueError) and message.startswith("插件已下架"):
            return "下架状态尚未绑定完整的重新上架审核报告。", "请先 `/recheck`，由维护者审阅重新上架报告及下架原因，再评论 `/approve`。"
        if isinstance(exc, ValueError):
            return "批准内容与当前申请、源码或已发布的审核报告不一致，或审核尚未完成。", "请先评论 `/recheck`，重新检查和审阅后再由维护者批准。"
        return "审核报告或市场索引发布失败，尚未确认上架成功。", "请由维护者查看发布日志。若已批准，请确认索引状态后重试 `/approve`；勿修改或重复发布同一版本。"
    if stage == "reject":
        return "拒绝结果未能完整发布，尚未确认处理成功。", "请由维护者查看日志，确认版本记录和申请状态后再处理。"
    return "市场任务运行环境或 GitHub 服务异常，尚未完成检查。", retry


def record_failure(work, stage, exc):
    reason, next_step = failure_detail(stage, exc)
    write_json(work / "failure.json", {"stage": stage, "reason": reason, "next_step": next_step})


def failure(args, api):
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    slug = os.environ["GITHUB_REPOSITORY"]
    issue, allowed = authorized_issue(api, slug, event)
    if not allowed:
        return
    number = issue["number"]
    results = json.loads(os.environ.get("MARKET_JOB_RESULTS", "{}"))
    stage = next((name for name in STAGES if results.get(name, {}).get("result") == "failure"), "unknown")
    reason, next_step = failure_detail(stage, RuntimeError())
    status = "检查失败"
    conclusion = "申请保持开启，尚未确认上架成功。"
    for path in args.work.rglob("failure.json"):
        detail = read_json(path)
        if detail.get("stage") == stage:
            reason, next_step = detail["reason"], detail["next_step"]
            break
    # push 成功后才写该证据。后续标签、评论、关闭失败不能反向宣称索引未发布。
    from .cli import issue_request
    for path in args.work.rglob("publication.json"):
        publication = read_json(path)
        if (publication.get("stage") == stage and publication.get("request") == issue_request(issue.get("body", ""))
                and publication.get("status") in {"已上架", "已拒绝"}):
            status = publication["status"]
            reason = f"版本记录已成功推送（{status}），但后续标签、评论或关闭申请未全部完成。"
            next_step = "无需修改或重新发布插件，请维护者核对已发布记录并完成申请处理。"
            conclusion = "以已发布的版本记录为准。"
            break
    run_id = str(int(os.environ["GITHUB_RUN_ID"]))
    attempt = str(int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
    marker = f"<!-- lubi-market-failure:{run_id}:{attempt} -->"
    safe_set_status(api, slug, number, status)
    # 同一轮失败任务重试不重复通知；真正的新一轮检查仍会新增评论。
    if any(marker in comment.get("body", "") and comment.get("user", {}).get("login") == "github-actions[bot]"
           and comment.get("user", {}).get("type") == "Bot"
           for comment in api.pages(f"/repos/{slug}/issues/{number}/comments")):
        return
    api.comment(slug, number, f"❌ {STAGES.get(stage, '市场任务')}未完成\n\n**原因：** {reason}\n\n"
                f"**下一步：** {next_step}\n\n{conclusion}\n\n"
                f"[查看详细日志](https://github.com/{slug}/actions/runs/{run_id})\n\n{marker}")
