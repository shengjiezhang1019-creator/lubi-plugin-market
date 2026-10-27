import base64
import copy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from nacl.public import PrivateKey, SealedBox

from market.github import GitHubAPIError
from market.security import canonical
from market.security_enable import CLIENT_KEYS
from market.security_setup import SECRET, VARIABLE, backup_key, setup


class FakeAPI:
    def __init__(self):
        self.server = PrivateKey.generate()
        self.files = {}
        self.secret = None
        self.variable = None
        self.admin = True
        self.operations = []
        self.pending_tree = None
        self.fail_push = False

    def request(self, path, method="GET", data=None):
        self.operations.append((path, method))
        relative = path.removeprefix("/repos/example/market")
        if method == "GET":
            if relative == "":
                return {"default_branch": "main", "permissions": {"admin": self.admin}}
            if relative == "/actions/workflows/security-enable.yml":
                return {"id": 1}
            if relative.startswith("/contents/"):
                name = relative.split("/contents/", 1)[1].split("?", 1)[0]
                if name in self.files:
                    return {"encoding": "base64", "content": base64.b64encode(json.dumps(self.files[name]).encode()).decode()}
            elif relative == "/actions/secrets/" + SECRET and self.secret:
                return {"name": SECRET}
            elif relative == "/actions/variables/" + VARIABLE and self.variable:
                return {"name": VARIABLE, "value": self.variable}
            elif relative == "/actions/secrets/public-key":
                return {"key_id": "server", "key": base64.b64encode(bytes(self.server.public_key)).decode()}
            elif relative == "/git/ref/heads/main":
                return {"object": {"sha": "a" * 40}}
            elif relative == "/git/commits/" + "a" * 40:
                return {"tree": {"sha": "b" * 40}}
            elif relative.endswith("/runs?per_page=5"):
                return {"workflow_runs": []}
            raise GitHubAPIError(404, method, path)
        if method == "PUT" and relative == "/actions/secrets/" + SECRET:
            assert data["key_id"] == "server"
            self.secret = SealedBox(self.server).decrypt(base64.b64decode(data["encrypted_value"])).decode()
            return None
        if method == "POST" and relative == "/actions/variables":
            self.variable = data["value"]
            return None
        if method == "POST" and relative == "/git/trees":
            self.pending_tree = data["tree"]
            assert all("private" not in v["path"] for v in self.pending_tree)
            return {"sha": "c" * 40}
        if method == "POST" and relative == "/git/commits":
            return {"sha": "d" * 40}
        if method == "PATCH" and relative == "/git/refs/heads/main":
            assert data["force"] is False
            if self.fail_push:
                raise GitHubAPIError(422, method, path)
            for item in self.pending_tree:
                self.files[item["path"]] = json.loads(item["content"])
            return None
        if method == "POST" and relative.endswith("/dispatches"):
            assert data == {"ref": "main"}
            return None
        raise AssertionError((path, method))


def configured(api):
    private = Ed25519PrivateKey.generate()
    api.secret = base64.b64encode(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    api.variable = "existing-key"
    api.files["security_keys.json"] = {"existing-key": base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}
    payload = {"revision": 3, "records": [{"id": "existing-ban", "status": "blocked"}]}
    api.files["security.json"] = {"key_id": "existing-key", "payload": payload,
                                  "signature": base64.b64encode(private.sign(canonical(payload))).decode()}


def test_new_market_automatically_saves_secret_keys_and_dispatches(tmp_path):
    api = FakeAPI()
    path = tmp_path / "outside-code/private.json"
    result = setup(api, "example/market", path)
    backup = json.loads(path.read_text())
    assert api.secret == backup["private_key"]
    assert api.variable == result["key_id"]
    assert api.files[CLIENT_KEYS] == api.files["security_keys.json"]
    assert not any(api.secret in json.dumps(content) for content in api.files.values())
    assert not any("security.json" == name for name in api.files)
    assert api.operations[-1][0].endswith("/dispatches")


def test_existing_market_does_not_replace_key_or_clear_bans(tmp_path):
    api = FakeAPI()
    configured(api)
    before = copy.deepcopy(api.files)
    private_before = api.secret
    setup(api, "example/market", tmp_path / "must-not-exist/private.json")
    assert not (tmp_path / "must-not-exist").exists()
    assert api.secret == private_before
    assert api.files["security_keys.json"] == before["security_keys.json"]
    assert api.files["security.json"] == before["security.json"]
    assert not any(method == "PUT" for _, method in api.operations)
    # 重复运行只检查并触发工作流，不再提交公钥文件。
    api.operations.clear()
    setup(api, "example/market", tmp_path / "must-not-exist/private.json")
    assert not any("/git/" in path for path, method in api.operations)


def test_admin_authorization_required_before_any_write(tmp_path):
    api = FakeAPI()
    api.admin = False
    with pytest.raises(ValueError, match="管理员"):
        setup(api, "example/market", tmp_path / "private.json")
    assert not list(tmp_path.iterdir())
    assert all(method == "GET" for _, method in api.operations)


def test_unknown_existing_secret_is_not_overwritten(tmp_path):
    api = FakeAPI()
    api.secret = "unknown-existing-private-key"
    with pytest.raises(ValueError, match="不会重新生成"):
        setup(api, "example/market", tmp_path / "private.json")
    assert api.secret == "unknown-existing-private-key"
    assert all(method == "GET" for _, method in api.operations)


def test_recover_after_public_file_publish_failure_reuses_private_backup(tmp_path):
    api = FakeAPI()
    api.fail_push = True
    backup = tmp_path / "private.json"
    with pytest.raises(GitHubAPIError):
        setup(api, "example/market", backup)
    original = api.secret
    assert backup.exists()
    assert not api.files
    api.fail_push = False
    setup(api, "example/market", backup)
    assert api.secret == original
    assert CLIENT_KEYS in api.files


def test_wrong_backup_repository_is_rejected(tmp_path):
    path = tmp_path / "private.json"
    backup_key(path, "another/market")
    with pytest.raises(ValueError, match="其他市场"):
        backup_key(path, "example/market")


@pytest.mark.parametrize("slug", ["../market", "example/../market", "https://github.com/a/b", "a/b\n"])
def test_invalid_repository_never_uses_api(tmp_path, slug):
    api = FakeAPI()
    with pytest.raises(ValueError):
        setup(api, slug, tmp_path / "private.json")
    assert not api.operations
