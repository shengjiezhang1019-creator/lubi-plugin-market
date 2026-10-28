from __future__ import annotations

from pathlib import Path

from .archive import download, unpack
from .common import COMMIT, object_digest, validate_request, write_json
from .dependencies import declaration_report
from .github import GitHub
from .relisting import review_context
from .requests import check_existing_request
from .rules.component_types import analyze_component_types
from .rules.hook_bindings import analyze_hook_bindings
from .validation import framework_check, manifest_check, rules_fingerprint


def validate(root, request, work, *, static_only=False, archive_data=None, commit=None, release=None, resume_issue=None):
    root, work = Path(root), Path(work)
    request = validate_request(request)
    check_existing_request(root, request, resume_issue=resume_issue, resume_status="approved")
    relisting = review_context(root, request)
    if archive_data is None:
        release, commit = GitHub().release(request["repository"], request["tag"])
        archive_data = download(request["repository"], commit)
    if not COMMIT.fullmatch(commit or ""):
        raise ValueError("commit 不合法")
    source, sha256 = unpack(archive_data, work / "source")
    manifest = manifest_check(source, request)
    component_analysis = analyze_component_types(source)
    checks = framework_check(source, component_analysis=component_analysis)
    hook_analysis = analyze_hook_bindings(source, request.get("hooks"))
    checks.append({"name": "hook_bindings", "status": "warn" if hook_analysis["status"] == "needs_review" else "pass",
                   "message": "Hook 申报与源码声明静态对比，仅供人工核实；实际挂载以宿主加载结果为准",
                   "location": ""})
    if any(r["status"] == "fail" for r in checks):
        raise ValueError("框架静态检查失败：" + str(checks))
    dependencies = declaration_report(manifest)
    report = {"schema_version": 1, "status": "static_only" if static_only else "validated", "request": request,
              "commit": commit, "sha256": sha256, "manifest": manifest, "checks": checks,
              "component_analysis": component_analysis,
              "hook_analysis": hook_analysis,
              "rules_fingerprint": rules_fingerprint(), "dependency_status": "skipped" if static_only else "runtime",
              "dependency_policy": dependencies, "release_notes": (release or {}).get("body", ""),
              "published_at": (release or {}).get("published_at", ""),
              "source_dir": str(source.relative_to(work).as_posix())}
    if relisting is not None:
        report["relisting"] = relisting
    report["fingerprint"] = object_digest(report)
    write_json(work / "validation.json", report)
    return report, source
