"""市场可见性状态，未填写状态的历史登记默认仍为正常上架。"""


def is_active(entry):
    status = entry.get("status", "active")
    if status not in {"active", "withdrawn"}:
        raise ValueError("插件登记状态不合法，不能继续发布")
    return status == "active"
