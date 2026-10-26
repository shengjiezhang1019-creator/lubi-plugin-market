"""确认 Git 推送和默认分支上的最终申请结果。"""
import base64
import json
from urllib.parse import quote

from .github import GitHubAPIError


def push_index(root, message, paths, run):
    # 不依赖本地“已批准”判断是否推送，覆盖上次 commit/push 中断后的重试。
    run(["git", "add", "--", *paths], cwd=root, check=True)
    staged = run(["git", "diff", "--cached", "--quiet", "--", *paths], cwd=root, check=False)
    if staged.returncode == 1:
        run(["git", "commit", "-m", message, "--", *paths], cwd=root, check=True)
    elif staged.returncode != 0:
        raise RuntimeError("无法确认索引暂存状态，停止发布")
    run(["git", "push"], cwd=root, check=True)


def published_result(api, slug, request, issue_number, branch):
    if not branch:
        branch = api.request(f"/repos/{slug}")["default_branch"]
    path = f"versions/{request['plugin_name']}/{request['tag'][1:]}.json"
    try:
        response = api.request(f"/repos/{slug}/contents/{path}?ref={quote(branch, safe='')}")
    except GitHubAPIError as exc:
        if exc.status == 404:
            return None
        raise
    if response.get("encoding") != "base64":
        raise ValueError("远程版本记录编码不符合预期")
    record = json.loads(base64.b64decode(response["content"]))
    original_request = record.get("request") or record.get("validation", {}).get("request")
    if (record.get("status") in {"approved", "rejected"} and record.get("issue_number") == issue_number
            and original_request == request):
        return record
    return None
