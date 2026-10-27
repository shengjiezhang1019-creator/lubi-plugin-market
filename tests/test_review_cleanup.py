"""评论过滤、状态展示和辅助类分析的离线回归，不操作真实申请。"""
import base64
import json
from pathlib import Path

import pytest
import yaml

from market import cli
from market.common import read_json
from test_feedback import Api, context


@pytest.mark.parametrize("command", ["/approve", "  /approve\n", "/reject", "\n /reject \n"])
def test_admin_decision_keeps_review_status_until_final_result(tmp_path, monkeypatch, command):
    args, _ = context(tmp_path, monkeypatch, command)
    api = Api()
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    monkeypatch.setattr(cli, "previous_ai", lambda *a: {})
    statuses = []
    monkeypatch.setattr(cli, "set_status", lambda *a: statuses.append(a[-1]))
    cli.gate(args)
    assert not statuses
    assert "eligible=true" in (tmp_path / "output").read_text()
    assert read_json(args.work / "request.json")["plugin_name"] == "sample"


def test_new_application_still_shows_review_in_progress(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "GitHub", lambda: Api())
    statuses = []
    monkeypatch.setattr(cli, "set_status", lambda *a: statuses.append(a[-1]))
    cli.gate(args)
    assert statuses == ["检查中"]


def test_regular_comment_has_no_writes_and_workflow_pre_filters_comments(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch, "谢谢，稍后查看")
    api = Api()
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    cli.gate(args)
    assert "mode=ignored" in (tmp_path / "output").read_text()
    assert all(method == "GET" for _, method, _ in api.calls)
    condition = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/market.yml").read_text(encoding="utf-8"))["jobs"]["gate"]["if"]
    assert "contains(github.event.comment.body, '/approve')" in condition
    assert "contains(github.event.comment.body, '/reject')" in condition


def test_each_phase_reads_latest_trusted_report_instead_of_caching(tmp_path):
    def comment(value, login="github-actions[bot]", kind="Bot"):
        payload = base64.b64encode(json.dumps({"summary": value}).encode()).decode()
        body = cli.MARKER + json.dumps({"fingerprint": value}) + " -->\n<!-- lubi-ai-base64:" + payload + " -->"
        return {"user": {"login": login, "type": kind}, "body": body}

    class Comments:
        rows = [comment("old"), comment("forged", login="author", kind="User")]
        reads = 0

        def pages(self, path):
            self.reads += 1
            return iter(self.rows)

    api = Comments()
    assert cli.previous_ai(api, "a/market", 1, tmp_path)["summary"] == "old"
    api.rows.append(comment("new"))
    assert cli.trusted_marker(api, "a/market", 1)["fingerprint"] == "new"
    assert api.reads == 2
