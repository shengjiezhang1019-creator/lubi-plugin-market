"""真实 AI 审核冒烟测试：只审示例，不发布评论、批准或修改正式索引。"""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from .ai_review import review
from .common import COMMIT, object_digest, read_json, write_json
from .pipeline import validate


def run(root: Path, work: Path, commit: str):
    if not COMMIT.fullmatch(commit):
        raise ValueError("冒烟测试必须绑定市场仓库完整 commit")
    work.mkdir(parents=True, exist_ok=True)
    example = root / "examples/demo_plugin"
    manifest = read_json(example / "_manifest.json")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for file in sorted(example.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                archive.write(file, "demo/" + file.relative_to(example).as_posix())
    with tempfile.TemporaryDirectory(dir=work, prefix="metadata-") as directory:
        metadata = Path(directory)
        write_json(metadata / "registry.json", {"plugins": {}})
        validation, source = validate(metadata, {
            "plugin_name": manifest["name"],
            "repository": "https://github.com/shengjiezhang1019-creator/lubi-plugin-market",
            "tag": "v" + manifest["version"],
        }, work, static_only=True, archive_data=buffer.getvalue(), commit=commit)
    validation["test_scope"] = "examples/demo_plugin only; no published plugin Release; cannot approve"
    validation.pop("fingerprint")
    validation["fingerprint"] = object_digest(validation)
    write_json(work / "validation.json", validation)
    report = review(source, validation, root / "prompts/plugin-review.md")
    write_json(work / "ai-review.json", report)
    if report["status"] != "completed":
        raise ValueError("AI 未完成全部文件审核，冒烟测试失败")
    print(json.dumps({"status": "passed", "model": report["model"], "files_read": report["files_read"],
                      "findings": len(report["findings"]), "published": False}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=Path(".work/ai-smoke"))
    args = parser.parse_args()
    run(Path.cwd(), args.work, os.environ.get("GITHUB_SHA", ""))
