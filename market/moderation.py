"""维护者手动下架/恢复整个插件；不删除版本，不操作用户本机。"""
import argparse
import copy
import os
from pathlib import Path
import subprocess

from .availability import is_active
from .common import NAME, now, read_json, write_json
from .github import GitHub
from .index import build_catalog
from .review_report import text


def set_availability(root, plugin_name, action, actor, reason):
    root = Path(root)
    if not isinstance(plugin_name, str) or not NAME.fullmatch(plugin_name):
        raise ValueError("插件名不合法")
    if action not in {"withdraw", "restore"}:
        raise ValueError("只支持 withdraw 或 restore")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("缺少操作人")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise ValueError("下架和恢复均需填写原因，最多 1000 字符")
    original = read_json(root / "registry.json")
    updated = copy.deepcopy(original)
    entry = updated["plugins"].get(plugin_name)
    if entry is None:
        raise ValueError("插件未登记，不能下架或恢复")
    desired_active = action == "restore"
    if is_active(entry) == desired_active:
        return False
    previous_catalog = read_json(root / "catalog.json") if (root / "catalog.json").exists() else None
    operation = {"action": action, "actor": actor, "reason": reason.strip(), "at": now()}
    entry["status"] = "active" if desired_active else "withdrawn"
    entry["moderation"] = operation
    entry.setdefault("moderation_history", []).append(operation)
    try:
        write_json(root / "registry.json", updated)
        build_catalog(root)
    except Exception:
        write_json(root / "registry.json", original)
        if previous_catalog is not None:
            write_json(root / "catalog.json", previous_catalog)
        else:
            (root / "catalog.json").unlink(missing_ok=True)
        raise
    return True


def run(root):
    # 工作流输入只作为数据，不拼进 shell；批准/拒绝仍使用原有流程。
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("只允许维护者手动运行工作流")
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    branch = event["repository"]["default_branch"]
    if os.environ.get("GITHUB_REF") != "refs/heads/" + branch:
        raise ValueError("请在市场默认分支运行管理操作")
    slug = os.environ["GITHUB_REPOSITORY"]
    actor = os.environ.get("GITHUB_TRIGGERING_ACTOR") or os.environ.get("GITHUB_ACTOR", "")
    api = GitHub()
    if not api.permission(slug, actor):
        raise ValueError("只有市场 write/admin 维护者可以下架或恢复")
    inputs = event["inputs"]
    name, action, reason = inputs["plugin_name"], inputs["action"], inputs["reason"]
    changed = set_availability(root, name, action, actor, reason)
    if changed:
        subprocess.run(["git", "add", "registry.json", "catalog.json"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", f"Market {action}: {name}"], cwd=root, check=True)
        # 无强推；失败时不能输出已发布成功。
        subprocess.run(["git", "push"], cwd=root, check=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"## 插件市场管理完成\n\n插件：{text(name)}\n\n操作：{text(action)}\n\n"
                         f"操作人：{text(actor)}\n\n原因：{text(reason)}\n\n"
                         + ("状态和目录已发布。" if changed else "当前已是目标状态，无需重复修改。")
                         + "\n\n用户已安装的副本不受此操作影响。\n")
    return changed


def main():
    parser = argparse.ArgumentParser(description="插件市场下架与恢复")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.root.resolve())


if __name__ == "__main__":
    main()
