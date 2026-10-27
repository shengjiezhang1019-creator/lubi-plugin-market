import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from market.common import read_json, write_json
from market.security import canonical, read_payload
from market.security_enable import CLIENT_KEYS, enable, run


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    raw = base64.b64encode(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return private, raw, public


def test_first_enable_creates_signed_notification_and_client_file(tmp_path, keys):
    _, raw, public = keys
    result = enable(tmp_path, private_key=raw, key_id="test")
    assert result == {"revision": 1, "records": 0, "key_id": "test"}
    assert read_payload(tmp_path) == {"revision": 1, "records": []}
    assert read_json(tmp_path / CLIENT_KEYS) == {"test": public}
    assert not any("private" in p.name for p in tmp_path.rglob("*"))


def test_repeat_enable_preserves_existing_bans_exactly(tmp_path, keys):
    private, raw, public = keys
    write_json(tmp_path / "security_keys.json", {"test": public})
    payload = {"revision": 8, "records": [{"id": "real-ban", "status": "blocked"}]}
    write_json(tmp_path / "security.json", {"key_id": "test", "payload": payload,
               "signature": base64.b64encode(private.sign(canonical(payload))).decode()})
    before = (tmp_path / "security.json").read_bytes()
    assert enable(tmp_path, private_key=raw, key_id="test")["records"] == 1
    assert (tmp_path / "security.json").read_bytes() == before
    assert enable(tmp_path, private_key=raw, key_id="test")["revision"] == 8


def test_wrong_private_key_never_changes_published_key_or_feed(tmp_path, keys):
    _, raw, _ = keys
    enable(tmp_path, private_key=raw, key_id="test")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    other = Ed25519PrivateKey.generate()
    wrong = base64.b64encode(other.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    with pytest.raises(ValueError, match="不会自动更换"):
        enable(tmp_path, private_key=wrong, key_id="test")
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("private,key_id", [("", ""), ("", "test")])
def test_missing_first_authorization_has_clear_instruction(tmp_path, private, key_id):
    with pytest.raises(ValueError, match="market.security_setup"):
        enable(tmp_path, private_key=private, key_id=key_id)
    assert not list(tmp_path.iterdir())


def test_enable_requires_permission_before_touching_files(tmp_path, monkeypatch):
    from market import security_enable
    event = tmp_path / "event.json"
    write_json(event, {"repository": {"default_branch": "main"}})
    values = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
              "GITHUB_EVENT_PATH": str(event), "GITHUB_ACTOR": "outsider", "GITHUB_REPOSITORY": "a/repo"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(security_enable.GitHub, "permission", lambda *a: False)
    with pytest.raises(ValueError, match="维护者"):
        run(tmp_path)
    assert not (tmp_path / "security_keys.json").exists()


def test_enable_workflow_uses_same_publication_lock():
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/security-enable.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["enable"]
    assert job["concurrency"] == {"group": "market-index-write", "cancel-in-progress": False}
    assert job["permissions"] == {"contents": "write"}
