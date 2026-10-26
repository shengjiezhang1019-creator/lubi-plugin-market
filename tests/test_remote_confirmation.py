"""用本地 bare 仓库和伪 API 验证远程确认，不连接 GitHub。"""
import base64
import json
import subprocess

import pytest

from market import cli, feedback
from market.common import write_json
from market.github import GitHubAPIError
from market.publication import push_index
from test_feedback import Api, context
from test_publish import prepare


@pytest.mark.parametrize("interrupted_step", ["commit", "push"])
def test_local_git_retry_publishes_commit_before_success(tmp_path, interrupted_step):
    remote, root = tmp_path / "remote.git", tmp_path / "work"
    def git(*args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    git("init", "--bare", str(remote))
    git("init", "-b", "main", str(root))
    git("config", "user.name", "Offline test", cwd=root)
    git("config", "user.email", "test@example.invalid", cwd=root)
    git("remote", "add", "origin", str(remote), cwd=root)
    (root / "versions").mkdir()
    (root / "versions/.gitkeep").write_text("")
    write_json(root / "registry.json", {"plugins": {}})
    write_json(root / "catalog.json", [])
    git("add", ".", cwd=root)
    git("commit", "-m", "Initial", cwd=root)
    git("push", "-u", "origin", "main", cwd=root)
    write_json(root / "versions/demo/1.0.0.json", {"status": "approved"})
    paths = ["registry.json", "catalog.json", "versions"]
    failed = False
    def run(command, **kwargs):
        nonlocal failed
        if command[1] == interrupted_step and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, command)
        return subprocess.run(command, **kwargs, capture_output=True)
    with pytest.raises(subprocess.CalledProcessError):
        push_index(root, "Approve", paths, run)
    push_index(root, "Approve", paths, run)
    published = git("--git-dir", str(remote), "show", "refs/heads/main:versions/demo/1.0.0.json")
    assert json.loads(published.stdout)["status"] == "approved"
    assert git("rev-parse", "HEAD", cwd=root).stdout.strip() == git("--git-dir", str(remote), "rev-parse", "main").stdout.strip()
    head = git("rev-parse", "HEAD", cwd=root).stdout
    push_index(root, "Approve", paths, run)
    assert git("rev-parse", "HEAD", cwd=root).stdout == head


def test_retry_after_push_failure_retries_push_and_does_not_repeat_commit(tmp_path, monkeypatch):
    args, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    calls = []
    def run(command, **kwargs):
        calls.append(command[1])
        if command == ["git", "push"] and calls.count("push") == 1:
            raise RuntimeError("push failed")
        return subprocess.CompletedProcess(command, 1 if command[1] == "diff" and calls.count("commit") == 0 else 0)
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "set_status", lambda *a: None)
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: calls.append("comment"))
    monkeypatch.setattr(cli.GitHub, "request", lambda *a: calls.append("close"))
    with pytest.raises(RuntimeError, match="push failed"):
        cli.publish(args)
    assert "comment" not in calls
    cli.publish(args)
    assert calls == ["add", "diff", "commit", "push", "add", "diff", "push", "comment", "close"]


class RemoteApi(Api):
    def __init__(self, record, error=None):
        super().__init__()
        self.record, self.error = record, error

    def request(self, path, method="GET", data=None):
        if "/contents/" in path:
            self.calls.append((path, method, data))
            if self.error:
                raise GitHubAPIError(self.error, method, path)
            return {"encoding": "base64", "content": base64.b64encode(json.dumps(self.record).encode()).decode()}
        if path == "/repos/a/market":
            return {"default_branch": "main"}
        return super().request(path, method, data)


def terminal(tmp_path, monkeypatch, status):
    args, _ = context(tmp_path, monkeypatch, "/recheck")
    request = cli.issue_request(Api().issue["body"])
    record = {"status": status, "issue_number": 1, "request": request}
    write_json(tmp_path / "versions/sample/1.0.0.json", record)
    return args, record


@pytest.mark.parametrize("status,label", [("approved", "已上架"), ("rejected", "需修改后重新申请")])
def test_original_issue_recheck_keeps_remote_terminal_state(tmp_path, monkeypatch, status, label):
    args, record = terminal(tmp_path, monkeypatch, status)
    api = RemoteApi(record)
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    registry = (tmp_path / "registry.json").read_bytes()
    cli.gate(args)
    assert "mode=ignored" in (tmp_path / "output").read_text()
    assert any(data == {"labels": [label]} for _, _, data in api.calls)
    assert any(data == {"state": "closed"} for _, _, data in api.calls)
    assert len(api.comments) == 1
    assert "无需重复审核" in api.comments[0]["body"]
    assert "尚未确认上架" not in api.comments[0]["body"]
    assert registry == (tmp_path / "registry.json").read_bytes()
    cli.gate(args)
    assert len(api.comments) == 1


def test_failure_fallback_preserves_existing_remote_approval(tmp_path, monkeypatch):
    args, record = terminal(tmp_path, monkeypatch, "approved")
    api = RemoteApi(record)
    monkeypatch.setenv("MARKET_JOB_RESULTS", '{"gate":{"result":"failure"}}')
    feedback.failure(args, api)
    assert any(data == {"labels": ["已上架"]} for _, _, data in api.calls)
    assert "不符合提交要求" not in api.comments[0]["body"]


def test_local_only_record_cannot_be_reported_as_published(tmp_path, monkeypatch):
    args, record = terminal(tmp_path, monkeypatch, "approved")
    api = RemoteApi(record, error=404)
    assert feedback.complete_existing(args, api, "a/market", 1, record["request"], {}) is False
    assert not api.comments
    assert not any(data == {"state": "closed"} for _, _, data in api.calls)


@pytest.mark.parametrize("change", [{"issue_number": 99}, {"request": {}}, {"status": "pending"}])
def test_other_remote_result_cannot_finalize_current_issue(tmp_path, monkeypatch, change):
    args, record = terminal(tmp_path, monkeypatch, "approved")
    api = RemoteApi({**record, **change})
    assert feedback.complete_existing(args, api, "a/market", 1, record["request"], {}) is False
    assert not api.comments


def test_staging_inspection_failure_prevents_push():
    commands = []
    def run(command, **kwargs):
        commands.append(command[1])
        return subprocess.CompletedProcess(command, 2)
    with pytest.raises(RuntimeError, match="暂存状态"):
        push_index(None, "Approve", ["versions"], run)
    assert commands == ["add", "diff"]
