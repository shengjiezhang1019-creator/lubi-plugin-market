"""下架与恢复的离线回归，不调用真实 GitHub、不操作真实索引。"""
from types import SimpleNamespace

import pytest
import yaml

from market import cli, moderation
from market.availability import is_active
from market.common import assert_binding, object_digest, read_json, write_json
from market.index import approve, build_catalog


@pytest.fixture
def market(tmp_path):
    write_json(tmp_path / "registry.json", {"schema_version": 1, "plugins": {
        "demo": {"repository": "https://github.com/a/demo", "issue_number": 1},
        "peer": {"repository": "https://github.com/a/peer", "issue_number": 2},
    }})
    for name, versions in (("demo", ["1.9.0", "1.10.0"]), ("peer", ["1.0.0"])):
        for version in versions:
            validation = {"request": {"plugin_name": name, "repository": "https://github.com/a/" + name, "tag": "v" + version},
                          "manifest": {"name": name, "version": version}, "commit": "a" * 40, "sha256": "b" * 64,
                          "release_notes": "notes", "published_at": "date"}
            write_json(tmp_path / "versions" / name / (version + ".json"), {"status": "approved", "validation": validation})
    build_catalog(tmp_path)
    return tmp_path


def version_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in (root / "versions").rglob("*.json")}


def test_withdraw_and_restore_preserve_history_and_latest(market):
    before = version_bytes(market)
    assert moderation.set_availability(market, "demo", "withdraw", "maintainer", "需修复问题")
    entry = read_json(market / "registry.json")["plugins"]["demo"]
    assert entry["status"] == "withdrawn"
    assert entry["moderation"]["actor"] == "maintainer"
    assert entry["moderation"]["reason"] == "需修复问题"
    assert entry["moderation"]["at"]
    assert entry["repository"] == "https://github.com/a/demo"
    assert [item["id"] for item in build_catalog(market)] == ["peer"]
    assert moderation.set_availability(market, "demo", "restore", "maintainer", "已核实可恢复")
    records = {item["id"]: item for item in read_json(market / "catalog.json")}
    assert records["demo"]["manifest"]["version"] == "1.10.0"
    assert len(read_json(market / "registry.json")["plugins"]["demo"]["moderation_history"]) == 2
    assert version_bytes(market) == before


def test_repeated_request_is_idempotent(market):
    assert moderation.set_availability(market, "demo", "restore", "maintainer", "already active") is False
    moderation.set_availability(market, "demo", "withdraw", "maintainer", "reason")
    before = (market / "registry.json").read_bytes()
    assert moderation.set_availability(market, "demo", "withdraw", "maintainer", "retry") is False
    assert (market / "registry.json").read_bytes() == before


@pytest.mark.parametrize("name,action,actor,reason", [
    ("../demo", "withdraw", "maintainer", "reason"), ("missing", "withdraw", "maintainer", "reason"),
    ("demo", "delete", "maintainer", "reason"), ("demo", "withdraw", "", "reason"),
    ("demo", "withdraw", "maintainer", " "), ("demo", "restore", "maintainer", "a" * 1001),
])
def test_invalid_operation_has_no_side_effects(market, name, action, actor, reason):
    before = (market / "registry.json").read_bytes(), (market / "catalog.json").read_bytes()
    with pytest.raises(ValueError):
        moderation.set_availability(market, name, action, actor, reason)
    assert before == ((market / "registry.json").read_bytes(), (market / "catalog.json").read_bytes())


def test_catalog_failure_rolls_back_state(market, monkeypatch):
    before = (market / "registry.json").read_bytes(), (market / "catalog.json").read_bytes()
    def broken(root):
        raise ValueError("broken catalog")
    monkeypatch.setattr(moderation, "build_catalog", broken)
    with pytest.raises(ValueError, match="broken catalog"):
        moderation.set_availability(market, "demo", "withdraw", "maintainer", "reason")
    assert before == ((market / "registry.json").read_bytes(), (market / "catalog.json").read_bytes())


def test_withdrawn_blocks_manual_approval_and_keeps_name_binding(market):
    moderation.set_availability(market, "demo", "withdraw", "maintainer", "reason")
    request = {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": "v2.0.0"}
    with pytest.raises(ValueError, match="已下架"):
        assert_binding(market, request)
    with pytest.raises(ValueError, match="绑定"):
        assert_binding(market, {**request, "repository": "https://github.com/other/demo"})
    validation = {"status": "validated", "dependency_status": "runtime", "request": request,
                  "commit": "c" * 40, "manifest": {"version": "2.0.0"}}
    validation["fingerprint"] = object_digest(validation)
    ai = {"status": "completed", "commit": "c" * 40, "validation_fingerprint": validation["fingerprint"]}
    with pytest.raises(ValueError, match="已下架"):
        approve(market, validation, ai, "maintainer", 3)
    assert not (market / "versions/demo/2.0.0.json").exists()


def test_discovery_skips_withdrawn_repository(market, monkeypatch):
    moderation.set_availability(market, "demo", "withdraw", "maintainer", "reason")
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/market")
    called = []
    def pages(self, path):
        called.append(path)
        assert "/repos/a/demo/releases" not in path
        return []
    monkeypatch.setattr(cli.GitHub, "pages", pages)
    cli.discover(SimpleNamespace(root=market, work=market / "work"))
    assert "/repos/a/peer/releases" in called


def environment(market, monkeypatch):
    event = market / "event.json"
    write_json(event, {"repository": {"default_branch": "main"},
                       "inputs": {"plugin_name": "demo", "action": "withdraw", "reason": "reason"}})
    for key, value in {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(event),
                       "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": "a/market", "GITHUB_ACTOR": "maintainer",
                       "GITHUB_TRIGGERING_ACTOR": "maintainer", "GITHUB_STEP_SUMMARY": str(market / "summary.md")}.items():
        monkeypatch.setenv(key, value)


def test_workflow_permission_denied_before_mutation(market, monkeypatch):
    environment(market, monkeypatch)
    monkeypatch.setattr(moderation.GitHub, "permission", lambda *args: False)
    with pytest.raises(ValueError, match="write/admin"):
        moderation.run(market)
    assert is_active(read_json(market / "registry.json")["plugins"]["demo"])
    assert not (market / "summary.md").exists()


@pytest.mark.parametrize("event,ref", [("issues", "refs/heads/main"), ("workflow_dispatch", "refs/heads/other")])
def test_workflow_requires_manual_default_branch(market, monkeypatch, event, ref):
    environment(market, monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REF", ref)
    with pytest.raises(ValueError):
        moderation.run(market)


@pytest.mark.parametrize("push_fails", [False, True])
def test_workflow_reports_success_only_after_push(market, monkeypatch, push_fails):
    environment(market, monkeypatch)
    actors = []
    monkeypatch.setattr(moderation.GitHub, "permission", lambda self, slug, actor: actors.append(actor) or True)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command == ["git", "push"] and push_fails:
            raise RuntimeError("push failed")
    monkeypatch.setattr(moderation.subprocess, "run", run)
    if push_fails:
        with pytest.raises(RuntimeError, match="push failed"):
            moderation.run(market)
        assert not (market / "summary.md").exists()
    else:
        assert moderation.run(market)
        assert "状态和目录已发布" in (market / "summary.md").read_text(encoding="utf-8")
    assert actors == ["maintainer"]
    assert commands[-1] == ["git", "push"]


def test_workflow_serializes_with_approval_and_keeps_inputs_out_of_shell():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / ".github/workflows/moderate.yml"
    form = yaml.safe_load(path.read_text(encoding="utf-8"))
    dispatch = form.get("on", form.get(True))["workflow_dispatch"]
    assert dispatch["inputs"]["reason"]["required"] is True
    assert dispatch["inputs"]["action"]["options"] == ["withdraw", "restore"]
    assert form["jobs"]["moderate"]["concurrency"]["group"] == "market-index-write"
    assert "${{ inputs." not in path.read_text(encoding="utf-8")
