"""冒烟测试保持不可批准状态，未完成阅读时失败；不调用真实模型。"""
from pathlib import Path

import pytest

from market import smoke
from market.common import read_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_smoke_never_publishes_or_claims_formal_release(tmp_path, monkeypatch, status):
    def review(source, validation, prompt):
        assert validation["status"] == "static_only"
        assert validation["commit"] == "a" * 40
        return {"status": status, "model": "test-model", "files_read": ["plugin.py", "_manifest.json", "README.md"], "findings": []}
    monkeypatch.setattr(smoke, "review", review)
    if status == "incomplete":
        with pytest.raises(ValueError, match="未完成"):
            smoke.run(ROOT, tmp_path, "a" * 40)
    else:
        smoke.run(ROOT, tmp_path, "a" * 40)
    assert read_json(tmp_path / "validation.json")["status"] == "static_only"
    assert read_json(tmp_path / "ai-review.json")["status"] == status
