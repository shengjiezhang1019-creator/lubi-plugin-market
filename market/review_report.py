"""把已校验的 AI 报告整理为 GitHub 展示，建议不参与批准逻辑。"""
from collections import Counter
import html
import json
import re
from urllib.parse import quote


LEVELS = {"high": "高风险", "medium": "中风险", "question": "待确认", "low": "低风险"}


def text(value):
    """待审源码和模型内容只能作为文字，不能注入标题、链接或 HTML。"""
    value = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", value)


def recommendation(validation, ai):
    """依据报告事实给出保守建议，不改变审核状态或执行批准。"""
    if (validation.get("status") != "validated" or validation.get("dependency_status") != "runtime"
            or ai.get("status") != "completed" or not ai.get("files_read")
            or ai.get("commit") != validation.get("commit")
            or ai.get("validation_fingerprint") != validation.get("fingerprint")):
        return "暂不接受", "审核未完成或报告绑定信息不足，请补齐检查后再审。"
    findings = ai.get("findings")
    if not isinstance(findings, list) or any(f.get("severity") not in LEVELS for f in findings):
        return "暂不接受", "风险信息不完整，不能形成接受建议。"
    counts = Counter(f["severity"] for f in findings)
    checks = validation.get("checks", [])
    if any(c.get("status") == "fail" for c in checks):
        return "暂不接受", "存在未通过的静态检查，需修复后重新审核。"
    if counts["high"]:
        return "暂不接受", f"AI 报告列出 {counts['high']} 项高风险，请维护者核实证据并要求修复后重审。"
    if counts["medium"] or counts["question"]:
        return "修改后再审", f"仍有 {counts['medium']} 项中风险、{counts['question']} 项待确认，需要修复或提供说明，再由维护者复核。"
    if any(c.get("status") == "warn" for c in checks):
        return "修改后再审", "静态检查仍有警告，请先处理或说明，再由维护者复核。"
    if counts["low"]:
        return "建议接受（低风险需跟进）", f"当前报告仅列出 {counts['low']} 项低风险，未列出高风险、中风险或待确认项；维护者仍需确认可接受。"
    return "建议接受", "当前已完成报告未列出风险项，静态检查无失败或警告；这不等于经过运行验证或安全认证。"


def render_review(validation, ai, request):
    decision, reason = recommendation(validation, ai)
    findings = ai.get("findings", [])
    counts = Counter(f.get("severity") for f in findings)
    warnings = [c for c in validation.get("checks", []) if c.get("status") == "warn"]
    lines = ["## 插件审核参考报告", "", f"**收录建议：{decision}**", "", reason, "",
             "建议由系统依据 AI 风险项及静态结果归纳，不是批准决定，不会自动上架。最终由维护者人工核实并批准。", "",
             "### 审核概况", "", "| 项目 | 结果 |", "| --- | --- |",
             f"| 插件 | {text(request['plugin_name'])} |", f"| 版本 | {text(request['tag'])} |",
             f"| 源码提交 | {text(validation['commit'])} |",
             "| 静态组件分类 | " + text(
                 ", ".join(validation.get("component_analysis", {}).get("types", [])) or "未发现 / 未确定"
             ) + "（仅源码识别，运行时注册仍需验证） |",
             f"| 静态与依赖声明检查 | {'通过（含警告）' if warnings else '通过'} |",
             f"| AI 审核 | {'已完成' if ai.get('status') == 'completed' else '未完成'} |",
             f"| AI 声明已读 | {len(ai.get('files_read', []))} 个文件 |",
             f"| 风险分布 | 高 {counts['high']} / 中 {counts['medium']} / 待确认 {counts['question']} / 低 {counts['low']} |",
             "", "### 功能摘要", "", text(ai.get("summary", "未提供")), "", "### 发现的问题", ""]
    if not findings:
        lines.append("本次报告未列出风险项，不代表不存在未发现的问题。")
    rank = {level: index for index, level in enumerate(LEVELS)}
    for index, finding in enumerate(sorted(findings, key=lambda f: rank.get(f.get("severity"), 99)), 1):
        file = str(finding.get("file", ""))
        line = finding.get("line", "")
        url = request["repository"].rstrip("/") + "/blob/" + quote(validation["commit"], safe="") + "/" + quote(file, safe="/") + "#L" + str(line)
        lines += [f"#### {index}. {LEVELS.get(finding.get('severity'), '未知风险')}", "",
                  f"位置：[{text(file)}:{line}]({url})", "",
                  "**证据**", "", text(finding.get("evidence", "")), "",
                  "**可能影响**", "", text(finding.get("impact", "")), "",
                  "**处理建议**", "", text(finding.get("suggestion", "")), ""]
    if warnings:
        lines += ["### 静态检查警告", ""]
        for warning in warnings:
            lines += ["- " + text(warning.get("name", "")) + "：" + text(warning.get("message", ""))]
    lines += ["", "### 审核限制", "", text(ai.get("limitations", "未提供")), "",
              "本报告基于源码静态审核，不代表已运行插件、完成业务测试或验证所有外部服务。", "",
              "<details>", "<summary>AI 声明已阅读的文件</summary>", ""]
    lines += ["- " + text(file) for file in ai.get("files_read", [])]
    # JSON 中转义斜线和 @，避免评论发布层的命令/提及过滤改变原始 JSON 语义。
    raw = (json.dumps(ai, ensure_ascii=False, indent=2).replace("/", "\\u002f").replace("@", "\\u0040")
           .replace("<", "\\u003c").replace(">", "\\u003e"))
    fence = "`" * max(3, max((len(s) + 1 for s in re.findall(r"`+", raw)), default=3))
    lines += ["", "</details>", "", "<details>", "<summary>查看原始结构化 AI 报告</summary>", "",
              fence + "json", raw, fence, "", "</details>", ""]
    return "\n".join(lines)
