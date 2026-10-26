"""复核发现的异常组合：标签、拒绝重试和提示词绑定。全程离线。"""
import pytest

from market import cli, feedback, validation
from market.common import object_digest, read_json, write_json
from test_feedback import Api, context
from test_publish import prepare


def test_label_failure_still_notifies_author(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    api = Api()
    monkeypatch.setattr(feedback, "set_status", lambda *a: (_ for _ in ()).throw(RuntimeError("secret")))
    feedback.failure(args, api)
    assert len(api.comments) == 1
    assert "查看详细日志" in api.comments[0]["body"]
    assert "secret" not in api.comments[0]["body"]


def test_reject_retry_completes_existing_result_without_overwriting(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch, "/reject 改了的原因")
    api = Api()
    request = cli.issue_request(api.issue["body"])
    path = args.root / "versions/sample/1.0.0.json"
    record = {"status": "rejected", "request": request, "actor": "first-maintainer",
              "reason": "原始拒绝原因", "issue_number": 1}
    write_json(path, record)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    monkeypatch.setattr(cli, "snapshot_issue", lambda *a: (api.issue, request))
    commands = []
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kw: commands.append(command[1]) or cli.subprocess.CompletedProcess(command, 0))
    statuses, comments, closes = [], [], []
    monkeypatch.setattr(cli, "set_status", lambda *a: statuses.append(a[-1]))
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: comments.append(a[-1]))
    monkeypatch.setattr(cli.GitHub, "request", lambda *a: closes.append(a[-1]))
    cli.reject(args)
    assert read_json(path) == record
    assert commands == ["add", "diff", "push"]
    assert statuses == ["已拒绝"]
    assert "原始拒绝原因" in comments[0]
    assert closes == [{"state": "closed"}]
    assert read_json(args.work / "publication.json")["status"] == "已拒绝"


@pytest.mark.parametrize("change", [{"status": "approved"}, {"issue_number": 99}, {"request": {}}])
def test_reject_retry_cannot_overwrite_other_records(tmp_path, monkeypatch, change):
    args, _ = context(tmp_path, monkeypatch, "/reject 原因")
    request = cli.issue_request(Api().issue["body"])
    record = {"status": "rejected", "request": request, "issue_number": 1, "reason": "原原因", **change}
    path = args.root / "versions/sample/1.0.0.json"
    write_json(path, record)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    monkeypatch.setattr(cli, "snapshot_issue", lambda *a: ({}, request))
    with pytest.raises(ValueError, match="不能用拒绝命令覆盖"):
        cli.reject(args)
    assert read_json(path) == record


@pytest.mark.parametrize("hash_value", [None, "old prompt"])
def test_final_approval_blocks_old_or_unbound_prompt(tmp_path, monkeypatch, hash_value):
    args, marker = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    report = read_json(args.work / "previous-ai.json")
    if hash_value is None:
        report.pop("prompt_sha256")
    else:
        report["prompt_sha256"] = object_digest(hash_value)
    write_json(args.work / "previous-ai.json", report)
    marker["ai_sha256"] = object_digest(report)
    with pytest.raises(ValueError, match="提示词"):
        cli.publish(args)
    assert not (args.root / "versions").exists()


def test_current_ai_report_also_requires_current_prompt(tmp_path, monkeypatch):
    args, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    report = read_json(args.work / "ai-review.json")
    report["prompt_sha256"] = object_digest("old prompt")
    write_json(args.work / "ai-review.json", report)
    with pytest.raises(ValueError, match="提示词"):
        cli.publish(args)


@pytest.mark.parametrize("hash_value", [None, "old prompt", "current prompt"])
def test_reused_report_checks_current_prompt(tmp_path, monkeypatch, hash_value):
    from types import SimpleNamespace
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.py").write_text("pass", encoding="utf-8")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/plugin-review.md").write_text("current prompt", encoding="utf-8")
    write_json(tmp_path / "validation.json", {"commit": "a" * 40, "source_dir": "source"})
    report = {"status": "completed", "commit": "a" * 40, "summary": "摘要", "limitations": "未运行",
              "files_read": ["plugin.py"], "findings": []}
    if hash_value:
        report["prompt_sha256"] = object_digest(hash_value)
    write_json(tmp_path / "previous-ai.json", report)
    args = SimpleNamespace(action="ai", root=tmp_path, work=tmp_path)
    if hash_value == "current prompt":
        cli.run_action(args)
        assert read_json(tmp_path / "ai-review.json")["status"] == "completed"
    else:
        with pytest.raises(ValueError, match="提示词"):
            cli.run_action(args)


def test_prompt_change_invalidates_rules_fingerprint(tmp_path, monkeypatch):
    prompt = tmp_path / "review.md"
    prompt.write_text("old", encoding="utf-8")
    monkeypatch.setattr(validation, "REVIEW_PROMPT", prompt)
    before = validation.rules_fingerprint()
    prompt.write_text("new", encoding="utf-8")
    assert before != validation.rules_fingerprint()
