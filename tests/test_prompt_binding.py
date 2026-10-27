"""审核政策变更后旧报告不能继续批准；全部离线。"""
import pytest

from market import validation
from market.cli import assert_prompt_unchanged
from market.common import object_digest


def test_prompt_changes_rules_fingerprint(tmp_path, monkeypatch):
    prompt = tmp_path / "prompts/plugin-review.md"
    prompt.parent.mkdir(exist_ok=True)
    prompt.write_text("old", encoding="utf-8")
    monkeypatch.setattr(validation, "REVIEW_PROMPT", prompt)
    before = validation.rules_fingerprint()
    prompt.write_text("new", encoding="utf-8")
    assert validation.rules_fingerprint() != before
    prompt.unlink()
    with pytest.raises(FileNotFoundError):
        validation.rules_fingerprint()


@pytest.mark.parametrize("bound", [None, "outdated"])
def test_missing_or_changed_binding_rejected(tmp_path, bound):
    prompt = tmp_path / "prompts/plugin-review.md"
    prompt.parent.mkdir(exist_ok=True)
    prompt.write_text("current", encoding="utf-8")
    with pytest.raises(ValueError, match="提示词"):
        assert_prompt_unchanged({"prompt_sha256": bound}, tmp_path)


def test_unchanged_prompt_accepted(tmp_path):
    prompt = tmp_path / "prompts/plugin-review.md"
    prompt.parent.mkdir(exist_ok=True)
    prompt.write_text("current", encoding="utf-8")
    assert_prompt_unchanged({"prompt_sha256": object_digest("current")}, tmp_path)
