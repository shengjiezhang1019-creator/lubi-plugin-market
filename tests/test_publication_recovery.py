"""离线验证索引回滚、修复和重复版本入口。"""
import pytest

from market import cli, index
from market.common import read_json, write_json
from market.feedback import failure_detail
from market.pipeline import validate
from market.requests import ExistingVersionError, check_existing_request
from test_feedback import Api, context
from test_moderation import market as market
from test_relisting import report, request, withdraw


@pytest.mark.parametrize("target", ["registry.json", "catalog.json", "after_catalog"])
def test_failed_relisting_rolls_back_all_files_and_can_retry(market, monkeypatch, target):
    withdraw(market)
    validation, ai = report(market)
    before = {name: (market / name).read_bytes() for name in ("registry.json", "catalog.json")}
    original_write = index.write_json
    original_build = index.build_catalog
    def fail_write(path, data):
        if path.name == target:
            raise OSError("simulated write failure")
        original_write(path, data)
    def fail_after_catalog(root, **kwargs):
        original_build(root, **kwargs)
        raise OSError("simulated interruption")
    with monkeypatch.context() as patch:
        if target == "after_catalog":
            patch.setattr(index, "build_catalog", fail_after_catalog)
        else:
            patch.setattr(index, "write_json", fail_write)
        with pytest.raises(OSError):
            index.approve(market, validation, ai, "maintainer", 5)
    assert not (market / "versions/demo/2.0.0.json").exists()
    assert all((market / name).read_bytes() == data for name, data in before.items())
    assert index.approve(market, validation, ai, "maintainer", 5)
    assert next(x for x in read_json(market / "catalog.json") if x["id"] == "demo")["manifest"]["version"] == "2.0.0"


@pytest.mark.parametrize("damage", ["missing", "stale", "invalid"])
def test_approval_retry_repairs_catalog_and_requests_push(market, damage):
    withdraw(market)
    validation, ai = report(market)
    index.approve(market, validation, ai, "maintainer", 5)
    version_bytes = (market / "versions/demo/2.0.0.json").read_bytes()
    registry_bytes = (market / "registry.json").read_bytes()
    path = market / "catalog.json"
    if damage == "missing":
        path.unlink()
    elif damage == "stale":
        write_json(path, [])
    else:
        path.write_text("broken", encoding="utf-8")
    assert index.approve(market, validation, ai, "maintainer", 5) is True
    assert any(item["id"] == "demo" for item in read_json(path))
    assert (market / "versions/demo/2.0.0.json").read_bytes() == version_bytes
    assert (market / "registry.json").read_bytes() == registry_bytes
    assert index.approve(market, validation, ai, "maintainer", 5) is False


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_existing_version_is_rejected_before_network_or_ai(tmp_path, monkeypatch, status):
    root = tmp_path
    write_json(root / "registry.json", {"plugins": {}})
    record = {"status": status, "issue_number": 12, "request": request()}
    write_json(root / "versions/demo/2.0.0.json", record)
    monkeypatch.setattr("market.pipeline.GitHub", lambda *a: pytest.fail("重复版本不能访问网络"))
    with pytest.raises(ExistingVersionError) as error:
        validate(root, request(), root / "work")
    reason, next_step = failure_detail("validate", error.value)
    assert "不会重复送交 AI" in reason
    assert "#12" in next_step
    assert "新版本" in next_step


@pytest.mark.parametrize("status,command", [("approved", "/approve"), ("rejected", "/reject reason")])
def test_only_same_issue_can_resume_existing_result(tmp_path, status, command):
    write_json(tmp_path / "registry.json", {"plugins": {}})
    record = {"status": status, "issue_number": 12, "request": request()}
    write_json(tmp_path / "versions/demo/2.0.0.json", record)
    check_existing_request(tmp_path, request(), resume_issue=12, resume_status=status)
    with pytest.raises(ExistingVersionError):
        check_existing_request(tmp_path, request(), resume_issue=13, resume_status=status)
    with pytest.raises(ExistingVersionError):
        check_existing_request(tmp_path, request())


def test_gate_stops_duplicate_before_preparing_request(tmp_path, monkeypatch):
    args, _ = context(tmp_path, monkeypatch)
    api = Api()
    req = cli.issue_request(api.issue["body"])
    write_json(tmp_path / "versions/sample/1.0.0.json", {"status": "approved", "issue_number": 9,
               "validation": {"request": req}})
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    with pytest.raises(ExistingVersionError):
        cli.gate(args)
    assert not (args.work / "request.json").exists()


def test_same_issue_relisting_approval_revalidation_remains_allowed(market):
    withdraw(market)
    validation, ai = report(market)
    index.approve(market, validation, ai, "maintainer", 5)
    check_existing_request(market, request(), resume_issue=5, resume_status="approved")
    with pytest.raises(ExistingVersionError):
        check_existing_request(market, request(), resume_issue=6, resume_status="approved")
