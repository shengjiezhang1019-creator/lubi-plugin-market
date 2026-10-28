import json

import pytest

from market.common import object_digest, validate_request
from market.rules.hook_bindings import analyze_hook_bindings, parse_declared_hooks
from market.pipeline import validate
from market.review_report import render_review


def test_checkboxes_normalized_and_section_bounded():
    body = "### 使用的 Hook\n- [x] PFC_CONTEXT_GATHERING — 工具\n- [ ] KNOWLEDGE_QUERY\n- [X] PFC_CONTEXT_GATHERING\n### 补充说明\n- [x] OTHER"
    assert parse_declared_hooks(body) == ["PFC_CONTEXT_GATHERING"]
    assert parse_declared_hooks("legacy") is None


def test_literal_and_import_alias_definition(tmp_path):
    (tmp_path / "plugin.py").write_text(
        """
from src.plugin_system.base.mount_point_types import LifecycleBindingRequest as Bind
from src.plugin_system.base.hook_contracts.pfc_context_gathering import PFC_CONTEXT_GATHERING as POINT
class Plugin:
    def build_lifecycle_bindings(self):
        return (Bind(point_id=POINT.point_id, component=tool),)
""",
        encoding="utf-8",
    )
    result = analyze_hook_bindings(tmp_path, ["PFC_CONTEXT_GATHERING"])
    assert result["status"] == "consistent"
    assert result["observed"] == ["PFC_CONTEXT_GATHERING"]
    assert result["evidence"][0]["file"] == "plugin.py"


def test_mismatch_dynamic_and_no_execution(tmp_path):
    (tmp_path / "plugin.py").write_text(
        """
from src.plugin_system.base.mount_point_types import LifecycleBindingRequest
raise RuntimeError("must not execute")
class Plugin:
    def build_lifecycle_bindings(self):
        return make_bindings()
unused = LifecycleBindingRequest(point_id="KNOWLEDGE_QUERY", component=tool)
dynamic = LifecycleBindingRequest(point_id=resolve_hook(), component=tool)
""",
        encoding="utf-8",
    )
    result = analyze_hook_bindings(tmp_path, ["PFC_CONTEXT_GATHERING"])
    assert result["status"] == "needs_review"
    assert result["declared_not_observed"] == ["PFC_CONTEXT_GATHERING"]
    assert result["observed_not_declared"] == ["KNOWLEDGE_QUERY"]
    assert len(result["warnings"]) == 2


def test_unrelated_same_named_constructor_not_detected(tmp_path):
    (tmp_path / "plugin.py").write_text(
        'from other import LifecycleBindingRequest\nx = LifecycleBindingRequest(point_id="FAKE")'
    )
    assert analyze_hook_bindings(tmp_path, [])["observed"] == []


def test_module_alias_constant_and_missing_declaration(tmp_path):
    (tmp_path / "plugin.py").write_text(
        """
import src.plugin_system.base.mount_point_types as mounts
POINT = "KNOWLEDGE_QUERY"
class Plugin:
    def build_lifecycle_bindings(self):
        return (mounts.LifecycleBindingRequest(point_id=POINT, component=tool),)
""",
        encoding="utf-8",
    )
    result = analyze_hook_bindings(tmp_path)
    assert result["declared"] is None
    assert result["observed"] == ["KNOWLEDGE_QUERY"]
    assert result["status"] == "needs_review"


def test_reassigned_dynamic_constant_is_not_reported_as_resolved(tmp_path):
    (tmp_path / "plugin.py").write_text(
        """
from src.plugin_system.base.mount_point_types import LifecycleBindingRequest as Bind
POINT = "KNOWLEDGE_QUERY"
POINT = choose_at_runtime()
binding = Bind(point_id=POINT, component=tool)
""",
        encoding="utf-8",
    )
    result = analyze_hook_bindings(tmp_path, ["KNOWLEDGE_QUERY"])
    assert result["observed"] == []
    assert result["warnings"]


def test_report_escapes_untrusted_evidence():
    validation = {
        "commit": "a" * 40,
        "hook_analysis": {
            "status": "needs_review",
            "declared": [],
            "observed": [],
            "declared_not_observed": [],
            "observed_not_declared": [],
            "evidence": [{"file": "<script>.py", "line": 1, "hook": "PFC_CONTEXT_GATHERING"}],
            "warnings": ["<img src=x>"],
        },
    }
    output = render_review(validation, {"findings": []}, {"plugin_name": "demo", "tag": "v1.0.0"})
    assert "<script>" not in output and "<img" not in output
    assert "&lt;script&gt;" in output


@pytest.mark.parametrize("hooks", ["PFC_CONTEXT_GATHERING", [1], ["bad name"], ["<script>"]])
def test_invalid_hook_request_rejected(hooks):
    with pytest.raises(ValueError):
        validate_request(
            {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": "v1.0.0", "hooks": hooks}
        )


def test_declaration_changes_bound_request_digest():
    request = {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": "v1.0.0"}
    assert object_digest({**request, "hooks": []}) != object_digest({**request, "hooks": ["KNOWLEDGE_QUERY"]})


def test_pipeline_records_warning_and_fingerprints_without_blocking(tmp_path):
    import io
    import zipfile

    root = tmp_path / "market"
    root.mkdir()
    (root / "registry.json").write_text('{"plugins": {}}')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "repo/_manifest.json",
            json.dumps(
                {
                    "name": "demo_plugin",
                    "version": "1.0.0",
                    "description": "test",
                    "author": {"name": "test"},
                    "plugin_dependencies": [],
                    "license": "MIT",
                }
            ),
        )
        archive.writestr("repo/README.md", "test")
        archive.writestr(
            "repo/plugin.py", "from src.plugin_system import BasePlugin\nclass Demo(BasePlugin):\n    pass\n"
        )
    request = {
        "plugin_name": "demo_plugin",
        "repository": "https://github.com/a/demo",
        "tag": "v1.0.0",
        "hooks": ["PFC_CONTEXT_GATHERING"],
    }
    report, _ = validate(root, request, tmp_path / "work", archive_data=buffer.getvalue(), commit="a" * 40)
    assert report["status"] == "validated"
    assert report["hook_analysis"]["declared_not_observed"] == ["PFC_CONTEXT_GATHERING"]
    assert any(c["name"] == "hook_bindings" and c["status"] == "warn" for c in report["checks"])
    fingerprint = report.pop("fingerprint")
    assert fingerprint == object_digest(report)
    report["fingerprint"] = fingerprint
    ai = {
        "status": "completed",
        "commit": "a" * 40,
        "validation_fingerprint": fingerprint,
        "files_read": ["plugin.py"],
        "findings": [],
        "summary": "test",
    }
    rendered = render_review(report, ai, request)
    assert "Hook 申报核对" in rendered and "PFC" in rendered
