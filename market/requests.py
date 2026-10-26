"""重复版本入口检查；只允许原申请补完已有批准/拒绝结果。"""
from pathlib import Path

from .common import assert_binding, read_json


class ExistingVersionError(ValueError):
    def __init__(self, record):
        self.status = record.get("status")
        number = record.get("issue_number")
        self.issue_number = number if isinstance(number, int) and number > 0 else None
        super().__init__("该版本已有处理记录，不重复送审；修改源码请发布新版本")


def check_existing_request(root, request, *, resume_issue=None, resume_status=None):
    assert_binding(root, request, allow_withdrawn=True)
    path = Path(root) / "versions" / request["plugin_name"] / (request["tag"][1:] + ".json")
    if not path.is_file():
        return
    record = read_json(path)
    original_request = record.get("request") or record.get("validation", {}).get("request")
    if (resume_issue is not None and record.get("issue_number") == resume_issue
            and record.get("status") == resume_status and original_request == request):
        return
    raise ExistingVersionError(record)
