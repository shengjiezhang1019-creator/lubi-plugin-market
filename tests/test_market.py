from __future__ import annotations

import copy
import io
import zipfile
from pathlib import Path

import pytest

from market.ai_review import source_bundle, validate_response
from market.archive import unpack
from market.cli import issue_request, trusted_marker
from market.common import assert_binding, object_digest, read_json, repository, validate_request, write_json
from market.dependencies import declaration_report
from market.github import GitHub
from market.index import approve, build_catalog
from market.validation import manifest_check


@pytest.fixture
def root(tmp_path):
    write_json(tmp_path / "registry.json", {"schema_version": 1, "plugins": {}})
    write_json(tmp_path / "catalog.json", [])
    return tmp_path


def request(name="demo_plugin", tag="v1.0.0"):
    return {"plugin_name": name, "repository": "https://github.com/example/demo", "tag": tag}


def validated(tag="v1.0.0"):
    result = {"status": "validated", "dependency_status": "runtime", "request": request(tag=tag),
              "commit": "a" * 40, "sha256": "b" * 64,
              "manifest": {"name": "demo_plugin", "version": tag[1:]},
              "dependency_lock": {"packages": []}, "release_notes": "", "published_at": ""}
    result["fingerprint"] = object_digest(result)
    return result


def ai(validation):
    return {"status": "completed", "commit": validation["commit"], "validation_fingerprint": validation["fingerprint"]}


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        for path, data in entries:
            zip_file.writestr(path, data)
    return buffer.getvalue()


@pytest.mark.parametrize("url", ["https://github.com/a/b", "https://github.com/A/B.git/", " https://github.com/A/B "])
def test_repository_normalization(url):
    assert repository(url) == "https://github.com/a/b"


@pytest.mark.parametrize("url", ["http://github.com/a/b", "https://github.com.evil/a/b", "https://github.com/a/b/tree/main", "https://x:y@github.com/a/b", "https://github.com/a/.."])
def test_repository_rejects_other_sources(url):
    with pytest.raises(ValueError):
        repository(url)


@pytest.mark.parametrize("tag", ["main", "1.0.0", "v1.0.0-rc1", "v01.0.0", "v1.0"])
def test_request_requires_release_tag(tag):
    with pytest.raises(ValueError):
        validate_request(request(tag=tag))


def test_issue_form_cannot_inject_extra_shell_fields():
    body = "### 插件名称\n\ndemo_plugin\n\n### GitHub 仓库\n\nhttps://github.com/a/b\n\n### Release 标签\n\nv1.0.0\n\n### 说明\n\n; evil"
    assert issue_request(body)["tag"] == "v1.0.0"
    with pytest.raises(ValueError):
        issue_request(body.replace("demo_plugin", "demo;whoami"))


@pytest.mark.parametrize("bad", ["repo/../../escape.py", "/absolute/file", "repo/a\\b", "repo/C:evil", "repo/NUL.txt", "repo/file. "])
def test_archive_rejects_unsafe_paths(tmp_path, bad):
    data = archive([(bad.replace("\\", "/"), "x")])
    if "\\" in bad:
        # Windows ZipInfo 会规范化写入路径；直接改 ZIP 文件名才是真实恶意输入。
        data = data.replace(bad.replace("\\", "/").encode(), bad.encode())
    with pytest.raises(ValueError):
        unpack(data, tmp_path / "out")


def test_archive_requires_unique_root(tmp_path):
    with pytest.raises(ValueError):
        unpack(archive([("a/plugin.py", ""), ("b/plugin.py", "")]), tmp_path / "out")


def test_archive_rejects_symlink(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zip_file:
        info = zipfile.ZipInfo("root/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zip_file.writestr(info, "../../outside")
    with pytest.raises(ValueError):
        unpack(stream.getvalue(), tmp_path / "out")


def test_archive_preserves_all_files_for_review(tmp_path):
    path, sha = unpack(archive([("root/plugin.py", "raise RuntimeError('DO NOT IMPORT')"), ("root/.git/config", "secret")]), tmp_path / "out")
    assert (path / "plugin.py").is_file()
    assert (path / ".git/config").read_text() == "secret"
    assert len(sha) == 64


def test_manifest_does_not_execute_plugin(tmp_path):
    write_json(tmp_path / "_manifest.json", {"name": "demo_plugin", "version": "1.0.0", "python_dependencies": []})
    (tmp_path / "README.md").write_text("demo")
    (tmp_path / "plugin.py").write_text("raise RuntimeError('NEVER EXECUTE')")
    assert manifest_check(tmp_path, request())["name"] == "demo_plugin"


@pytest.mark.parametrize("dependencies", [[{"name": "x --index-url evil", "version": ">=1"}], [{"name": "httpx", "version": "@ https://evil"}], [{"name": "a", "version": ">=1"}, {"name": "A", "version": ">=1"}]])
def test_manifest_rejects_pip_options_and_duplicate_names(tmp_path, dependencies):
    write_json(tmp_path / "_manifest.json", {"name": "demo_plugin", "version": "1.0.0", "python_dependencies": dependencies})
    (tmp_path / "README.md").write_text("demo")
    (tmp_path / "plugin.py").write_text("")
    with pytest.raises(ValueError):
        manifest_check(tmp_path, request())


def test_binding_prevents_takeover_including_case(root):
    write_json(root / "registry.json", {"plugins": {"demo_plugin": {"repository": "https://github.com/a/b"}}})
    with pytest.raises(ValueError):
        assert_binding(root, request())
    with pytest.raises(ValueError):
        assert_binding(root, {**request("DEMO_PLUGIN"), "repository": "https://github.com/a/b"})


def test_approval_is_idempotent_and_catalog_uses_numeric_latest(root):
    for version in ("v1.9.0", "v1.10.0", "v1.2.0"):
        validation = validated(version)
        assert approve(root, validation, ai(validation), "maintainer", 1)
        assert not approve(root, validation, ai(validation), "maintainer", 1)
    assert read_json(root / "catalog.json")[0]["manifest"]["version"] == "1.10.0"


@pytest.mark.parametrize("status", ["static_only", "failed", "pending"])
def test_incomplete_validation_cannot_be_approved(root, status):
    validation = validated()
    validation["status"] = status
    with pytest.raises(ValueError):
        approve(root, validation, ai(validation), "maintainer", 1)
    assert read_json(root / "catalog.json") == []


def test_tampered_validation_and_wrong_ai_cannot_be_approved(root):
    validation = validated()
    tampered = copy.deepcopy(validation)
    tampered["sha256"] = "c" * 64
    with pytest.raises(ValueError):
        approve(root, tampered, ai(tampered), "maintainer", 1)
    with pytest.raises(ValueError):
        approve(root, validation, {**ai(validation), "status": "incomplete"}, "maintainer", 1)
    with pytest.raises(ValueError):
        approve(root, validation, {**ai(validation), "commit": "c" * 40}, "maintainer", 1)


def test_same_version_must_not_change_source(root):
    first = validated()
    approve(root, first, ai(first), "maintainer", 1)
    second = validated()
    second["commit"] = "c" * 40
    second.pop("fingerprint")
    second["fingerprint"] = object_digest(second)
    with pytest.raises(ValueError):
        approve(root, second, ai(second), "maintainer", 2)


def test_rejected_record_not_in_catalog(root):
    write_json(root / "registry.json", {"plugins": {"demo_plugin": {"repository": request()["repository"]}}})
    write_json(root / "versions/demo_plugin/1.0.0.json", {"status": "rejected"})
    assert build_catalog(root) == []


def test_ai_cannot_claim_other_commit_or_nonexistent_file():
    report = {"commit": "a" * 40, "status": "completed", "summary": "演示插件", "limitations": "非安全保证",
              "files_read": ["plugin.py", "_manifest.json"], "findings": []}
    assert validate_response(report, "a" * 40, set(report["files_read"])) == report
    with pytest.raises(ValueError):
        validate_response(report, "b" * 40, set(report["files_read"]))
    with pytest.raises(ValueError):
        validate_response(report, "a" * 40, {"plugin.py"})


def test_ai_input_is_not_silently_truncated(tmp_path):
    (tmp_path / "plugin.py").write_text("x" * 100)
    with pytest.raises(ValueError):
        source_bundle(tmp_path, max_bytes=10)


def test_only_bot_can_supply_approval_marker():
    class Api:
        def pages(self, path):
            return [{"user": {"login": "attacker", "type": "User"}, "body": '<!-- lubi-market-validation:{"fingerprint":"fake"} -->'}]
    with pytest.raises(ValueError):
        trusted_marker(Api(), "a/b", 1)


def test_comment_does_not_emit_commands_or_mentions(monkeypatch):
    output = []
    api = GitHub(token="unused")
    monkeypatch.setattr(api, "request", lambda path, method, data: output.append(data["body"]))
    api.comment("a/b", 1, "/approve\n@user")
    assert output == ["／approve\n＠user"]
    with pytest.raises(ValueError):
        api.comment("a/b", 1, "x" * 60001)


def test_dependency_policy_does_not_claim_installation_validation():
    report = declaration_report({"python_dependencies": [{"name": "jieba", "version": ">=0.42.1"}]})
    assert report["policy"] == "runtime-environment-v1"
    assert report["declared"] == [{"name": "jieba", "version": ">=0.42.1"}]
    assert report["installation_tested"] is False
    assert report["runtime_tested"] is False


def test_workflows_are_valid_yaml():
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[1]
    for file in (root / ".github/workflows").glob("*.yml"):
        result = yaml.load(file.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert "on" in result and "jobs" in result
    market = yaml.load((root / ".github/workflows/market.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert market["jobs"]["ai"]["permissions"]["contents"] == "read"
    assert market["jobs"]["publish"]["concurrency"]["group"] == "market-index-write"


def test_catalog_no_longer_distributes_fixed_dependency_lock(root):
    validation = validated()
    approve(root, validation, ai(validation), "maintainer", 1)
    catalog = read_json(root / "catalog.json")
    assert "dependency_lock" not in catalog[0]
    assert catalog[0]["dependency_policy"]["policy"] == "runtime-environment-v1"


def test_old_dependency_validation_policy_cannot_be_approved(root):
    validation = validated()
    validation["dependency_status"] = "resolved"
    validation.pop("fingerprint")
    validation["fingerprint"] = object_digest(validation)
    with pytest.raises(ValueError, match="依赖声明检查"):
        approve(root, validation, ai(validation), "maintainer", 1)
