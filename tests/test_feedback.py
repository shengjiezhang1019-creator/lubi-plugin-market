"""申请反馈离线测试：不联网、不发表评论、不执行远程发布。"""
import json
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
import yaml

from market import cli, feedback
from market.common import read_json, write_json
from market.github import GitHubAPIError


class Api:
    def __init__(self, issue=None, permission=True):
        self.issue = issue or {"number": 1, "state": "open", "user": {"login": "author"},
                               "labels": [{"name": "自定义标签"}, {"name": "检查中"}],
                               "body": "### 插件名称\n\nsample\n\n### GitHub 仓库\n\nhttps://github.com/a/b\n\n### Release 标签\n\nv1.0.0"}
        self.allowed = permission
        self.labels = set(feedback.LABELS)
        self.calls = []
        self.comments = []

    def permission(self, *args):
        return self.allowed

    def pages(self, *args):
        return iter(self.comments)

    def comment(self, slug, number, text):
        self.comments.append({"body": text, "user": {"login": "github-actions[bot]", "type": "Bot"}})

    def request(self, path, method="GET", data=None):
        self.calls.append((path, method, data))
        if "/issues/" in path and method == "GET":
            return self.issue
        if "/labels/" in path and "/issues/" not in path and method == "GET":
            if unquote(path.rsplit("/", 1)[1]) not in self.labels:
                raise GitHubAPIError(404, method, path)
            return {}
        if path.endswith("/labels") and "/issues/" not in path and method == "POST":
            self.labels.add(data["name"])
        return {}


def context(tmp_path, monkeypatch, comment=None):
    write_json(tmp_path / "registry.json", {"schema_version": 1, "plugins": {}})
    event = {"action": "opened", "issue": {"number": 1}}
    if comment:
        event["comment"] = {"body": comment, "user": {"login": "author"}}
    event_path = tmp_path / "event.json"
    write_json(event_path, event)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/market")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    return SimpleNamespace(work=tmp_path / "work", root=tmp_path), event


def test_status_replaces_only_owned_labels_and_creates_missing_label():
    api = Api()
    api.labels.remove("检查失败")
    feedback.set_status(api, "a/market", 1, "检查失败")
    assert "检查失败" in api.labels
    deleted = [unquote(path.rsplit("/", 1)[1]) for path, method, _ in api.calls if method == "DELETE"]
    assert deleted == ["检查中"]
    assert any(data == {"labels": ["检查失败"]} for _, method, data in api.calls if method == "POST")


def test_label_permission_error_is_not_mistaken_for_missing_label():
    api = Api()
    api.request = lambda *a: (_ for _ in ()).throw(GitHubAPIError(403, "GET", "/labels"))
    with pytest.raises(GitHubAPIError):
        feedback.set_status(api, "a/market", 1, "检查失败")


def test_malformed_application_has_gate_failure_feedback(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    api = Api()
    api.issue["body"] = "### 插件名称\n\nsample"
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    with pytest.raises(ValueError) as error:
        cli.gate(args)
    feedback.record_failure(args.work, "gate", error.value)
    monkeypatch.setenv("MARKET_JOB_RESULTS", json.dumps({"gate": {"result": "failure"}}))
    feedback.failure(args, api)
    assert "GitHub 仓库" in api.comments[-1]["body"]
    assert "编辑原申请" in api.comments[-1]["body"]
    assert "申请保持开启" in api.comments[-1]["body"]
    assert not any(method == "PATCH" for _, method, _ in api.calls)


@pytest.mark.parametrize("command", ["/approve", "/reject bad", "/recheck"])
def test_unauthorized_command_does_not_change_status_or_comment(tmp_path, monkeypatch, command):
    args, event = context(tmp_path, monkeypatch, command)
    api = Api(permission=False)
    api.issue["user"]["login"] = "another-author"
    api.issue["body"] = "malformed"
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    cli.gate(args)
    feedback.failure(args, api)
    assert "mode=ignored" in (tmp_path / "output").read_text()
    assert not api.comments
    assert all(method == "GET" for _, method, _ in api.calls)


def test_author_can_recheck_and_status_returns_to_checking(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch, "/recheck")
    api = Api(permission=False)
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    cli.gate(args)
    assert read_json(args.work / "request.json")["plugin_name"] == "sample"
    assert "mode=review" in (tmp_path / "output").read_text()
    assert any(data == {"labels": ["检查中"]} for _, _, data in api.calls)
    assert not api.comments


@pytest.mark.parametrize("stage,error,expected", [
    ("validate", ValueError("缺少必需文件 README.md"), "README.md"),
    ("validate", ValueError("manifest 不符合市场规则: private"), "字段规范"),
    ("validate", ValueError("manifest 不符合市场规则: 'license' is a required property"), "缺少必填字段：license"),
    ("ai", TimeoutError("Bearer secret123"), "无需修改插件"),
    ("ai", ValueError("AI 未提供有效文件阅读范围"), "完整性校验"),
    ("publish", RuntimeError("push failed: secret123"), "索引发布失败"),
])
def test_failure_reports_safe_reason_and_retry(tmp_path, monkeypatch, stage, error, expected):
    args, _ = context(tmp_path, monkeypatch)
    api = Api()
    feedback.record_failure(args.work, stage, error)
    monkeypatch.setenv("MARKET_JOB_RESULTS", json.dumps({stage: {"result": "failure"}}))
    feedback.failure(args, api)
    text = api.comments[-1]["body"]
    assert expected in text
    assert "secret123" not in text and "private" not in text
    assert "actions/runs/123" in text
    feedback.failure(args, api)
    assert len(api.comments) == 1


def test_setup_failure_without_artifact_gets_fallback_feedback(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    api = Api()
    monkeypatch.setenv("MARKET_JOB_RESULTS", json.dumps({"gate": {"result": "failure"}}))
    feedback.failure(args, api)
    assert "查看详细日志" in api.comments[0]["body"]


@pytest.mark.parametrize("stage,status", [("publish", "已上架"), ("reject", "已拒绝")])
def test_post_push_feedback_failure_preserves_published_status(tmp_path, monkeypatch, stage, status):
    args, _ = context(tmp_path, monkeypatch)
    api = Api()
    write_json(args.work / "publication.json", {"stage": stage, "status": status,
               "request": cli.issue_request(api.issue["body"])})
    monkeypatch.setenv("MARKET_JOB_RESULTS", json.dumps({stage: {"result": "failure"}}))
    feedback.failure(args, api)
    assert "版本记录已成功推送" in api.comments[-1]["body"]
    assert "尚未确认上架成功" not in api.comments[-1]["body"]
    assert any(data == {"labels": [status]} for _, _, data in api.calls)


def test_workflow_covers_gate_failures_and_transports_safe_context():
    from pathlib import Path
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/market.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "needs.gate.result == 'failure'" in jobs["failure"]["if"]
    assert jobs["gate"]["permissions"]["issues"] == "write"
    assert "eligible == 'true'" in jobs["validate"]["if"]
    for name in feedback.STAGES:
        upload = next(step for step in jobs[name]["steps"] if step.get("name") == "Save failure context")
        assert upload["if"] == "failure()"
        assert upload["with"]["name"] == "failure-" + name
        assert "failure.json" in upload["with"]["path"]
    assert jobs["failure"]["steps"][-1]["env"]["MARKET_JOB_RESULTS"] == "${{ toJSON(needs) }}"


def test_incomplete_ai_is_reported_at_ai_stage(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    args.action = "ai"
    write_json(args.work / "validation.json", {"commit": "a" * 40, "source_dir": "source"})
    monkeypatch.setattr(cli, "review", lambda *a: {"status": "incomplete"})
    with pytest.raises(ValueError, match="AI 审核未完成"):
        cli.run_action(args)


def test_cli_persists_early_gate_failure_without_leaking_exception(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["market", "gate", "--work", str(args.work)])
    monkeypatch.setattr(cli, "gate", lambda *a: (_ for _ in ()).throw(ValueError("申请缺少 GitHub 仓库")))
    with pytest.raises(ValueError):
        cli.main()
    assert read_json(args.work / "failure.json")["stage"] == "gate"


def test_reject_updates_status_only_after_push(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch, "/reject 不符合规范")
    request = {"plugin_name": "sample", "repository": "https://github.com/a/b", "tag": "v1.0.0"}
    output = []
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    monkeypatch.setattr(cli, "snapshot_issue", lambda *a: ({"state": "open"}, request))
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kw: output.append(command[1]))
    monkeypatch.setattr(cli, "set_status", lambda *a: output.append(a[-1]))
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: output.append("comment"))
    monkeypatch.setattr(cli.GitHub, "request", lambda *a: output.append("close"))
    cli.reject(args)
    assert output == ["add", "commit", "push", "已拒绝", "comment", "close"]
    assert read_json(args.work / "publication.json")["status"] == "已拒绝"


def test_report_transitions_to_pending_review_without_closing(tmp_path, monkeypatch):
    from test_review_report import reports
    args, _ = context(tmp_path, monkeypatch)
    request, validation, ai = reports()
    write_json(args.work / "validation.json", validation)
    write_json(args.work / "ai-review.json", ai)
    output = []
    monkeypatch.setattr(cli, "snapshot_issue", lambda *a: ({"state": "open"}, request))
    monkeypatch.setattr(cli, "set_status", lambda *a: output.append(a[-1]))
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: output.append(a[-1]))
    monkeypatch.setattr(cli.GitHub, "request", lambda *a: pytest.fail("不能关闭等待人工审核的申请"))
    cli.publish(args)
    assert "尚未上架" in output[0]
    assert output[1] == "待人工审核"
