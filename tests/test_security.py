import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from market.common import write_json
from market.index import build_catalog
from market.security import canonical, prepare, read_payload, run


@pytest.fixture
def setup(tmp_path):
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    write_json(tmp_path / "security_keys.json", {"test": public})
    write_json(tmp_path / "registry.json", {"plugins": {"demo": {"repository": "https://github.com/example/demo"}}})
    for version, commit in (("1.0.0", "a" * 40), ("1.1.0", "b" * 40)):
        write_json(tmp_path / "versions/demo" / (version + ".json"), {
            "status": "approved", "validation": {
                "request": {"plugin_name": "demo", "repository": "https://github.com/example/demo"},
                "manifest": {"name": "demo", "version": version}, "commit": commit,
                "sha256": "c" * 64, "release_notes": "", "published_at": "today",
            },
        })
    build_catalog(tmp_path)
    args = dict(action="ban", name="demo", version="1.1.0", record_id="", reason="测试封禁",
                advice="重启露比", actor="maintainer", private_key=private, key_id="test")
    return tmp_path, key, args


def test_ban_filters_only_bad_version_and_release_preserves_history(setup):
    root, key, args = setup
    versions = {p: p.read_bytes() for p in (root / "versions").rglob("*.json")}
    envelope, record_id = prepare(root, **args)
    key.public_key().verify(base64.b64decode(envelope["signature"]), canonical(envelope["payload"]))
    write_json(root / "security.json", envelope)
    assert build_catalog(root)[0]["manifest"]["version"] == "1.0.0"
    with pytest.raises(ValueError, match="已经封禁"):
        prepare(root, **args)
    second, _ = prepare(root, **{**args, "version": "1.0.0"})
    write_json(root / "security.json", second)
    assert build_catalog(root) == []
    released, _ = prepare(root, **{**args, "action": "unban", "record_id": record_id, "reason": "误判恢复"})
    write_json(root / "security.json", released)
    assert build_catalog(root)[0]["manifest"]["version"] == "1.1.0"
    record = next(r for r in read_payload(root)["records"] if r["id"] == record_id)
    assert [h["action"] for h in record["history"]] == ["ban", "unban"]
    assert all(p.read_bytes() == value for p, value in versions.items())


def test_tampered_signed_feed_never_used_for_catalog(setup):
    root, _, args = setup
    envelope, _ = prepare(root, **args)
    envelope["payload"]["records"][0]["reason"] = "tampered"
    write_json(root / "security.json", envelope)
    with pytest.raises(InvalidSignature):
        build_catalog(root)


def test_initialize_signed_empty_feed(setup):
    root, _, args = setup
    envelope, _ = prepare(root, **{**args, "action": "initialize"})
    write_json(root / "security.json", envelope)
    assert read_payload(root) == {"revision": 1, "records": []}
    assert build_catalog(root)[0]["manifest"]["version"] == "1.1.0"
    with pytest.raises(ValueError, match="已初始化"):
        prepare(root, **{**args, "action": "initialize"})


@pytest.mark.parametrize("changes", [{"name": "../demo"}, {"version": "../1.0.0"}, {"reason": ""},
                                      {"action": "unban", "record_id": "missing"}])
def test_invalid_operations_do_not_write(setup, changes):
    root, _, args = setup
    with pytest.raises((ValueError, FileNotFoundError)):
        prepare(root, **{**args, **changes})
    assert not (root / "security.json").exists()


def test_workflow_checks_permission_before_any_write(setup, monkeypatch):
    from market import security
    root, _, _ = setup
    event = root / "event.json"
    write_json(event, {"repository": {"default_branch": "main"}, "inputs": {}})
    for name, value in {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(event),
                        "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": "example/market",
                        "GITHUB_ACTOR": "outsider"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(security.GitHub, "permission", lambda *a: False)
    with pytest.raises(ValueError, match="维护者"):
        run(root)
    assert not (root / "security.json").exists()


def test_push_failure_never_reports_success(setup, monkeypatch):
    from market import security
    root, _, args = setup
    event = root / "event.json"
    write_json(event, {"repository": {"default_branch": "main"}, "inputs": {
        "action": "ban", "plugin_name": "demo", "version": "1.1.0", "reason": "test",
    }})
    summary = root / "summary.md"
    values = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(event),
              "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": "example/market", "GITHUB_ACTOR": "maintainer",
              "MARKET_SECURITY_PRIVATE_KEY": args["private_key"], "MARKET_SECURITY_KEY_ID": "test",
              "GITHUB_STEP_SUMMARY": str(summary)}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(security.GitHub, "permission", lambda *a: True)

    def git(arguments, **kwargs):
        if arguments == ["git", "push"]:
            raise RuntimeError("push failed")

    monkeypatch.setattr(security.subprocess, "run", git)
    with pytest.raises(RuntimeError, match="push failed"):
        run(root)
    assert not summary.exists()
