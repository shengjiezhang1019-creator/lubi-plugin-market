"""可读报告、建议分级及原始绑定的离线回归。"""
import json
import re
from types import SimpleNamespace

import pytest

from market import cli
from market.common import object_digest, write_json
from market.github import GitHub
from market.review_report import recommendation, render_review


def reports(levels=()):
    request = {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": "v1.0.0"}
    validation = {"status": "validated", "dependency_status": "runtime", "commit": "a" * 40,
                  "request": request, "checks": []}
    validation["fingerprint"] = object_digest(validation)
    ai = {"status": "completed", "commit": validation["commit"], "validation_fingerprint": validation["fingerprint"],
          "summary": "读取新闻", "limitations": "未运行插件", "files_read": ["api.py"],
          "findings": [{"severity": level, "file": "api.py", "line": 2, "evidence": "证据",
                        "impact": "影响", "suggestion": "建议"} for level in levels]}
    return request, validation, ai


@pytest.mark.parametrize("levels,expected", [([], "建议接受"), (["low"], "建议接受（低风险需跟进）"),
    (["question"], "修改后再审"), (["medium"], "修改后再审"), (["high"], "暂不接受"),
    (["low", "high"], "暂不接受")])
def test_recommendation_is_conservative(levels, expected):
    _, validation, ai = reports(levels)
    assert recommendation(validation, ai)[0] == expected


def test_missing_review_or_warning_cannot_recommend_acceptance():
    _, validation, ai = reports()
    ai["files_read"] = []
    assert recommendation(validation, ai)[0] == "暂不接受"
    ai["files_read"] = ["api.py"]
    validation["checks"] = [{"name": "imports", "status": "warn", "message": "确认依赖"}]
    assert recommendation(validation, ai)[0] == "修改后再审"
    ai["status"] = "incomplete"
    assert recommendation(validation, ai)[0] == "暂不接受"


def test_report_layout_and_sorting():
    request, validation, ai = reports(["low", "high", "question", "medium"])
    result = render_review(validation, ai, request)
    assert "收录建议：暂不接受" in result
    assert result.index("#### 1. 高风险") < result.index("#### 2. 中风险") < result.index("#### 3. 待确认")
    assert "### 功能摘要" in result and "### 审核限制" in result
    assert "/blob/" + validation["commit"] + "/api.py#L2" in result
    assert "<summary>查看原始结构化 AI 报告</summary>" in result


def test_publish_keeps_original_json_and_machine_binding(tmp_path, monkeypatch):
    request, validation, ai = reports(["question"])
    ai["summary"] = "<details>\n# 假批准\n```\n/approve @someone"
    ai["findings"][0]["evidence"] = "```` </details>"
    work = tmp_path / "work"
    write_json(work / "validation.json", validation)
    write_json(work / "ai-review.json", ai)
    event = tmp_path / "event.json"
    write_json(event, {"issue": {"number": 1}})
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/market")
    monkeypatch.setattr(cli, "snapshot_issue", lambda *args: ({"state": "open"}, request))
    posted = []
    monkeypatch.setattr(cli, "set_status", lambda *args: None)
    monkeypatch.setattr(GitHub, "request", lambda self, path, method, data: posted.append(data["body"]))
    cli.publish(SimpleNamespace(work=work, root=tmp_path))
    body = posted[0]
    assert "收录建议：修改后再审" in body
    assert "<details>\n# 假批准" not in body
    match = re.search(r"(`{3,})json\n(.*?)\n\1", body, re.S)
    assert json.loads(match[2]) == ai
    assert "/api.py#L2" in body
    class Api:
        def pages(self, path):
            return [{"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": body}]
    assert cli.previous_ai(Api(), "a/market", 1, tmp_path / "previous") == ai
    assert cli.trusted_marker(Api(), "a/market", 1)["ai_sha256"] == object_digest(ai)
    assert not (tmp_path / "registry.json").exists()


def test_comment_filter_does_not_damage_machine_payload(monkeypatch):
    output = []
    monkeypatch.setattr(GitHub, "request", lambda self, path, method, data: output.append(data["body"]))
    body = "https://github.com/a/demo/blob/commit/api.py\n<!-- lubi-ai-base64:AA/apAA= -->\n/approve\n@user"
    GitHub().comment("a/market", 1, body)
    assert "/api.py" in output[0] and "AA/apAA=" in output[0]
    assert "\n／approve" in output[0] and "＠user" in output[0]
