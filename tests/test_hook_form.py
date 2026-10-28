from pathlib import Path
import json
import yaml
from market.cli import issue_request

ROOT = Path(__file__).resolve().parents[1]


def test_hook_choices_match_published_contracts():
    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/plugin.yml").read_text(encoding="utf-8"))
    field = next(item for item in form["body"] if item.get("id") == "hooks")
    assert field["type"] == "checkboxes"
    assert "multiple" not in field["attributes"]
    assert field["validations"]["required"] is True
    assert "default" not in field["attributes"]
    hooks = json.loads((ROOT / "docs/hook-contracts.json").read_text(encoding="utf-8"))["hooks"]
    assert [option["label"] for option in field["attributes"]["options"]] == [
        h["id"] + " — " + h["label"] for h in hooks
    ]
    assert all(not option.get("required", False) for option in field["attributes"]["options"])
    text = (ROOT / "docs/hook-contracts.md").read_text(encoding="utf-8")
    assert all("## " + h["id"] in text for h in hooks)
    assert all("input" in h and "result" in h for h in hooks if h["kind"] == "lifecycle")
    assert any("hook-contracts.md" in item.get("attributes", {}).get("value", "") for item in form["body"])


def test_unchecked_free_text_is_not_a_hook_selection():
    body = "### 插件名称\n\ndemo\n\n### GitHub 仓库\n\nhttps://github.com/a/demo\n\n### Release 标签\n\nv1.0.0\n\n### 使用的 Hook\n\nBEFORE_REPLY_CONTEXT — 回复前补充参考资料"
    assert issue_request(body) == {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": "v1.0.0", "hooks": []}


def test_only_checked_hooks_enter_request():
    body = (
        "### 插件名称\n\ndemo\n\n### GitHub 仓库\n\nhttps://github.com/a/demo"
        "\n\n### Release 标签\n\nv1.0.0\n\n### 使用的 Hook\n\n"
        "- [x] BEFORE_REPLY_CONTEXT — 向回复 Prompt 追加参考资料\n"
        "- [ ] MESSAGE_INGRESS — 转换或拒绝外部平台消息\n"
        "- [x] KNOWLEDGE_QUERY — 查询知识并提供后续模型参考资料"
    )
    assert issue_request(body) == {"plugin_name": "demo", "repository": "https://github.com/a/demo", "tag": "v1.0.0",
                                   "hooks": ["BEFORE_REPLY_CONTEXT", "KNOWLEDGE_QUERY"]}
