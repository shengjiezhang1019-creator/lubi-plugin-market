"""离线验证最终批准边界，不发送 GitHub 评论、不执行 git push。"""
from types import SimpleNamespace

import pytest

from market import cli
from market.common import object_digest, read_json, write_json


def prepare(tmp_path, monkeypatch):
    root = tmp_path / "market"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts/plugin-review.md").write_text("current prompt", encoding="utf-8")
    work = tmp_path / "work"
    request = {"plugin_name": "demo_plugin", "repository": "https://github.com/a/b", "tag": "v1.0.0"}
    validation = {"status": "validated", "dependency_status": "runtime", "request": request,
                  "commit": "a" * 40, "sha256": "b" * 64, "manifest": {"name": "demo_plugin", "version": "1.0.0"},
                  "dependency_lock": {}, "release_notes": "", "published_at": ""}
    validation["fingerprint"] = object_digest(validation)
    ai = {"status": "completed", "commit": validation["commit"], "validation_fingerprint": validation["fingerprint"],
          "prompt_sha256": object_digest("current prompt")}
    write_json(work / "validation.json", validation)
    write_json(work / "ai-review.json", ai)
    write_json(work / "previous-ai.json", ai)
    write_json(root / "registry.json", {"schema_version": 1, "plugins": {}})
    event_path = tmp_path / "event.json"
    write_json(event_path, {"issue": {"number": 1}, "comment": {"body": "/approve", "user": {"login": "maintainer"}}})
    monkeypatch.setenv("GITHUB_REPOSITORY", "market/repo")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(cli, "snapshot_issue", lambda *a: ({"state": "open"}, request))
    marker = {"fingerprint": validation["fingerprint"], "request_sha256": object_digest(request), "ai_sha256": object_digest(ai)}
    monkeypatch.setattr(cli, "trusted_marker", lambda *a: marker)
    monkeypatch.setattr(cli, "assert_release_unchanged", lambda *a: None)
    return SimpleNamespace(root=root, work=work), marker


def test_publish_requires_permission(tmp_path, monkeypatch):
    args, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: False)
    with pytest.raises(ValueError, match="write/admin"):
        cli.publish(args)
    assert not (args.root / "versions").exists()


def test_publish_blocks_changed_reviewed_content(tmp_path, monkeypatch):
    args, marker = prepare(tmp_path, monkeypatch)
    marker["fingerprint"] = "c" * 64
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    with pytest.raises(ValueError, match="不一致"):
        cli.publish(args)
    assert not (args.root / "versions").exists()


def test_publish_failure_does_not_close_or_claim_success(tmp_path, monkeypatch):
    args, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    output = []
    def run(command, **kwargs):
        if command == ["git", "push"]:
            raise RuntimeError("push failed")
        return cli.subprocess.CompletedProcess(command, 1)
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: output.append("comment"))
    monkeypatch.setattr(cli.GitHub, "request", lambda *a: output.append("close"))
    with pytest.raises(RuntimeError, match="push failed"):
        cli.publish(args)
    assert output == []


def test_publish_commits_then_reports_success(tmp_path, monkeypatch):
    args, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    output = []
    monkeypatch.setattr(cli, "set_status", lambda *a: output.append("status:" + a[-1]))
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kwargs: output.append(command[1]) or cli.subprocess.CompletedProcess(command, 1))
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: output.append("comment"))
    monkeypatch.setattr(cli.GitHub, "request", lambda *a: output.append("close"))
    cli.publish(args)
    assert output == ["add", "diff", "commit", "push", "status:已上架", "comment", "close"]
    assert read_json(args.root / "catalog.json")[0]["id"] == "demo_plugin"
