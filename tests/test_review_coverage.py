"""全部归档文件送审，缺失阅读记录不得作为完整报告批准。"""

import json

import pytest

from market.ai_review import source_bundle, validate_response
from market.common import object_digest, write_json
from market.index import approve


def test_bundle_reads_hidden_extensionless_and_other_scripts(tmp_path):
    contents = {"plugin.py": "pass", "_manifest.json": "{}", "README.md": "readme",
                ".github/workflows/check.yml": "name: check", ".hidden": "hidden",
                "LICENSE": "license", "helper.sh": "echo test", "web.js": "alert(1)",
                "page.html": "<p>test</p>", "empty": ""}
    for name, text in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert {file["path"]: file["content"] for file in source_bundle(tmp_path)} == contents


@pytest.mark.parametrize("data", [b"\x89PNG\r\n", b"binary\x00data", b"\xff\xfe", b"data\x01"])
def test_unreadable_file_cannot_be_silently_skipped(tmp_path, data):
    (tmp_path / "asset").write_bytes(data)
    with pytest.raises(ValueError, match="asset"):
        source_bundle(tmp_path)


def report():
    return {"commit": "a" * 40, "status": "completed", "summary": "summary", "limitations": "limits",
            "files_read": ["plugin.py", "_manifest.json"], "findings": []}


def test_missing_file_downgrades_completion_and_blocks_approval(tmp_path):
    result = validate_response(report(), "a" * 40, {"plugin.py", "_manifest.json", "README.md", "helper.sh"})
    assert result["status"] == "incomplete"
    assert "README.md" in result["limitations"] and "helper.sh" in result["limitations"]
    validation = {"status": "validated", "dependency_status": "resolved", "commit": "a" * 40}
    validation["fingerprint"] = object_digest(validation)
    result["validation_fingerprint"] = validation["fingerprint"]
    with pytest.raises(ValueError, match="完整 AI 报告"):
        approve(tmp_path, validation, result, "maintainer", 1)
    assert not (tmp_path / "versions").exists()


def test_full_coverage_allows_completed():
    value = report()
    value["files_read"].append("README.md")
    assert validate_response(value, "a" * 40, set(value["files_read"]))["status"] == "completed"


@pytest.mark.parametrize("files", [["plugin.py", "plugin.py"], ["plugin.py", "missing"], [None], [{}]])
def test_invalid_read_inventory_rejected(files):
    value = report()
    value["files_read"] = files
    with pytest.raises(ValueError):
        validate_response(value, "a" * 40, {"plugin.py", "_manifest.json"})


def test_approval_rechecks_previous_report_against_all_files(tmp_path, monkeypatch):
    import sys
    from market import cli

    work = tmp_path / "work"
    source = work / "source"
    source.mkdir(parents=True)
    for name in ("plugin.py", "_manifest.json", "README.md"):
        (source / name).write_text("text", encoding="utf-8")
    write_json(work / "validation.json", {"source_dir": "source", "commit": "a" * 40})
    write_json(work / "previous-ai.json", report())
    monkeypatch.setattr(sys, "argv", ["market", "ai", "--root", str(tmp_path), "--work", str(work)])
    cli.main()
    result = json.loads((work / "ai-review.json").read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert "README.md" in result["limitations"]
