"""下架期间审核新版，明确批准后原子发布；全程离线。"""
import io
import json
import zipfile

import pytest

from market import cli, moderation
from market.common import object_digest, read_json, write_json
from market.index import approve, build_catalog
from market.pipeline import validate
from market.relisting import review_context
from test_feedback import Api, context
from test_moderation import market as market, version_bytes
from test_publish import prepare


def request(tag="v2.0.0"):
    return {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": tag}


def report(root, tag="v2.0.0"):
    data = {"status": "validated", "dependency_status": "runtime", "request": request(tag),
            "manifest": {"name": "demo", "version": tag[1:]}, "commit": "c" * 40, "sha256": "d" * 64,
            "release_notes": "fixed", "published_at": "date", "relisting": review_context(root, request(tag))}
    data["fingerprint"] = object_digest(data)
    ai = {"status": "completed", "commit": data["commit"], "validation_fingerprint": data["fingerprint"]}
    return data, ai


def withdraw(root):
    moderation.set_availability(root, "demo", "withdraw", "maintainer", "旧版需修复")


def test_withdrawn_new_version_can_be_validated_without_becoming_visible(market, tmp_path):
    withdraw(market)
    before = (market / "registry.json").read_bytes(), version_bytes(market)
    manifest = {"name": "demo", "version": "2.0.0", "description": "fix", "author": "author",
                "license": "MIT", "plugin_dependencies": []}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/_manifest.json", json.dumps(manifest))
        archive.writestr("demo/README.md", "fixed")
        archive.writestr("demo/plugin.py", "from src.plugin_system import BasePlugin\nclass Plugin(BasePlugin): pass")
    validation, _ = validate(market, request(), tmp_path / "review", archive_data=buffer.getvalue(), commit="c" * 40)
    assert validation["status"] == "validated"
    assert validation["relisting"]["withdrawal"]["reason"] == "旧版需修复"
    assert before == ((market / "registry.json").read_bytes(), version_bytes(market))
    assert all(item["id"] != "demo" for item in build_catalog(market))


@pytest.mark.parametrize("tag", ["v1.0.0", "v1.9.0", "v1.10.0"])
def test_old_or_existing_version_cannot_relist(market, tag):
    withdraw(market)
    with pytest.raises(ValueError, match="高于历史记录"):
        review_context(market, request(tag))


def test_rejected_higher_version_also_remains_reserved(market):
    withdraw(market)
    write_json(market / "versions/demo/3.0.0.json", {"status": "rejected"})
    with pytest.raises(ValueError, match="高于历史记录"):
        review_context(market, request())


def test_other_repository_cannot_claim_withdrawn_name(market):
    withdraw(market)
    with pytest.raises(ValueError, match="绑定其他仓库"):
        review_context(market, {**request(), "repository": "https://github.com/other/demo"})


def test_regular_approval_cannot_restore_withdrawn_plugin(market):
    withdraw(market)
    validation, ai = report(market)
    with pytest.raises(ValueError, match="已下架"):
        approve(market, validation, ai, "maintainer", 5)
    assert read_json(market / "registry.json")["plugins"]["demo"]["status"] == "withdrawn"


def test_explicit_approval_publishes_latest_and_preserves_history(market):
    withdraw(market)
    previous = version_bytes(market)
    validation, ai = report(market)
    assert approve(market, validation, ai, "maintainer", 5, allow_relisting=True)
    entry = read_json(market / "registry.json")["plugins"]["demo"]
    assert entry["status"] == "active"
    assert entry["moderation_history"][0]["action"] == "withdraw"
    assert entry["moderation_history"][-1]["action"] == "relist"
    assert entry["moderation"]["issue_number"] == 5
    assert next(item for item in build_catalog(market) if item["id"] == "demo")["manifest"]["version"] == "2.0.0"
    assert all(version_bytes(market)[path] == data for path, data in previous.items())
    assert review_context(market, request()) == validation["relisting"]
    assert approve(market, validation, ai, "maintainer", 5, allow_relisting=True) is False


def test_changed_withdrawal_requires_recheck_before_restoring(market):
    withdraw(market)
    validation, ai = report(market)
    registry = read_json(market / "registry.json")
    registry["plugins"]["demo"]["moderation"]["reason"] = "发现了新问题"
    write_json(market / "registry.json", registry)
    with pytest.raises(ValueError, match="状态或原因已变化"):
        approve(market, validation, ai, "maintainer", 5, allow_relisting=True)
    assert not (market / "versions/demo/2.0.0.json").exists()


def test_unbound_report_cannot_restore_plugin(market):
    withdraw(market)
    validation, _ = report(market)
    validation.pop("relisting")
    validation.pop("fingerprint")
    validation["fingerprint"] = object_digest(validation)
    ai = {"status": "completed", "commit": validation["commit"], "validation_fingerprint": validation["fingerprint"]}
    with pytest.raises(ValueError, match="重新上架申请"):
        approve(market, validation, ai, "maintainer", 5, allow_relisting=True)


def test_unprivileged_relist_command_is_ignored(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch, "/approve-relist")
    api = Api(permission=False)
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    cli.gate(args)
    assert "mode=ignored" in (tmp_path / "output").read_text()


def test_cli_explicit_command_and_push_failure_do_not_claim_success(tmp_path, monkeypatch):
    args, _ = prepare(tmp_path, monkeypatch)
    event = read_json(tmp_path / "event.json")
    event["comment"]["body"] = "/approve-relist"
    write_json(tmp_path / "event.json", event)
    monkeypatch.setattr(cli.GitHub, "permission", lambda *a: True)
    allowed = []
    monkeypatch.setattr(cli, "approve", lambda *a, **kw: allowed.append(kw["allow_relisting"]) or True)
    def run(command, **kw):
        if command == ["git", "push"]:
            raise RuntimeError("push failed")
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: pytest.fail("push 失败不能宣布重新上架"))
    with pytest.raises(RuntimeError, match="push failed"):
        cli.publish(args)
    assert allowed == [True]
    assert not (args.work / "publication.json").exists()


def test_review_report_shows_withdrawal_and_explicit_command(tmp_path, monkeypatch):
    from test_review_report import reports
    args, _ = context(tmp_path, monkeypatch)
    req, validation, ai = reports()
    validation.pop("fingerprint")
    validation["relisting"] = {"registry_sha256": "f" * 64, "withdrawal": {"reason": "旧版本有问题"}}
    validation["fingerprint"] = object_digest(validation)
    ai["validation_fingerprint"] = validation["fingerprint"]
    write_json(args.work / "validation.json", validation)
    write_json(args.work / "ai-review.json", ai)
    comments = []
    monkeypatch.setattr(cli, "snapshot_issue", lambda *a: ({}, req))
    monkeypatch.setattr(cli.GitHub, "comment", lambda *a: comments.append(a[-1]))
    monkeypatch.setattr(cli, "set_status", lambda *a: None)
    cli.publish(args)
    assert "下架插件重新上架申请" in comments[0]
    assert "旧版本有问题" in comments[0]
    assert "`/approve-relist`" in comments[0]
    assert "旧版本继续下架" in comments[0]
