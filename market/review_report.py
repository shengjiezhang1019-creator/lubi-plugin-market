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
    """AI 仅提供依据和建议，不替维护者决定要求修改或拒绝。"""
    if any(c.get("status") == "fail" for c in validation.get("checks", [])):
        return "不符合提交要求", "存在违反明确规则的项目，请按必须修改项修正并重新申请。"
    if (validation.get("status") != "validated" or validation.get("dependency_status") != "runtime"
            or ai.get("status") != "completed" or not ai.get("files_read")
            or ai.get("commit") != validation.get("commit")
            or ai.get("validation_fingerprint") != validation.get("fingerprint")):
        return "审核暂未完成", "检查或报告绑定尚不完整，请维护者重试或处理审核服务，无需据此要求作者修改插件。"
    findings = ai.get("findings")
    if not isinstance(findings, list) or any(f.get("severity") not in LEVELS for f in findings):
        return "审核暂未完成", "风险信息不完整，请维护者重新生成完整报告。"
    if findings:
        return "待人工审核", "AI 提供了修改建议及依据，请维护者核实；仅在人工确认需要修改后，才要求作者重新申请。"
    return "待人工审核", "AI 未列出风险项；静态提示供维护者参考，不自动要求修改。是否收录由维护者批准。"


def render_review(validation, ai, request):
    decision, reason = recommendation(validation, ai)
    findings = ai.get("findings", [])
    counts = Counter(f.get("severity") for f in findings)
    warnings = [c for c in validation.get("checks", []) if c.get("status") == "warn"]
    lines = ["## 插件审核参考报告", "", f"**审核状态：{decision}**", "", reason, "",
             "建议由系统依据 AI 风险项及静态结果归纳，不是批准决定，不会自动上架。最终由维护者人工核实并批准。", "",
             "### 审核概况", "", "| 项目 | 结果 |", "| --- | --- |",
             f"| 插件 | {text(request['plugin_name'])} |", f"| 版本 | {text(request['tag'])} |",
             f"| 源码提交 | {text(validation['commit'])} |",
             "| 静态组件分类 | " + text(
                 ", ".join(validation.get("component_analysis", {}).get("types", [])) or "未发现 / 未确定"
             ) + "（仅源码识别，运行时注册仍需验证） |",
             f"| 静态与依赖声明检查 | {'符合要求（含提示）' if warnings else '通过'} |",
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
                  "**AI 修改建议（待人工核实）**", "", text(finding.get("suggestion", "")), ""]
    hooks = validation.get("hook_analysis")
    if hooks is not None:
        def hook_list(values):
            return text(", ".join(values) or "无")

        lines += ["### Hook 申报核对", "", "| 项目 | 内容 |", "| --- | --- |",
                  "| 对比结果 | " + ("静态集合一致" if hooks["status"] == "consistent" else "需人工核实") + " |",
                  "| Issue 申报 | " + (hook_list(hooks["declared"]) if hooks["declared"] is not None else "未提供") + " |",
                  "| 源码静态发现 | " + hook_list(hooks["observed"]) + " |",
                  "| 已申报但未发现 | " + hook_list(hooks["declared_not_observed"]) + " |",
                  "| 已发现但未申报 | " + hook_list(hooks["observed_not_declared"]) + " |", "",
                  "静态集合一致也不证明已挂载；不一致或无法确认时由维护者核实，不据此自动拦截。"]
        for item in hooks["evidence"]:
            lines += ["- " + text(f"{item['file']}:{item['line']} — {item['hook']}")]
        lines += ["- " + text(warning) for warning in hooks["warnings"]]
    if warnings:
        lines += ["### 静态检查提示", ""]
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
