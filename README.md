# 露比插件市场

独立于露比主程序的 GitHub 市场项目。目标流程：

```text
作者发布公开 Release → Issue 申请 / 定时发现
→ 固定 commit 与 ZIP SHA256 → 框架静态检查 → 依赖声明检查
→ AI 阅读源码生成报告 → 维护者 /approve → 写入 registry、versions、catalog
```

AI 不批准、不执行插件。首次和每次版本更新都由具有市场仓库 write/admin 权限的维护者批准。
市场仓库已发布到 https://github.com/shengjiezhang1019-creator/lubi-plugin-market 。
当前仍为第一版，AI 审核服务已配置百炼 `deepseek-v4-pro-0813`，本地及 GitHub Actions 示例真实 AI 审核已通过，正式 Release 申请与人工批准收录链路尚待验证。

## 本地开始

```powershell
python -m pip install -e '.[dev]'
python -m pytest -q
python -m market.cli demo --work .work/demo
```

demo 不联网、不调用 AI、不安装依赖，只验证测试源码的结构与市场自带静态规则。
报告会标记 `static_only`，不能进入批准流程；不会给 catalog 添加虚构插件。

完整远程验证（先完成下面的配置）：

```powershell
python -m market.cli validate --request request.json --work .work/real
python -m market.cli ai --work .work/real
```

request.json：

```json
{"plugin_name":"weather_plugin","repository":"https://github.com/OWNER/weather-plugin","tag":"v1.0.0"}
```

## 需要维护者配置的真实环境

1. 检查规则已内置于 `market/rules/`，无需配置主程序仓库、固定 commit、镜像或 `HOST_READ_TOKEN`。
   规范变化时同步维护市场和主程序规则；审核报告记录规则指纹，规则变化需重新审核。
2. 不再维护市场统一固定依赖基线或生成 wheel 锁定清单；主程序在安装时读取实际环境并补齐缺失依赖。
   市场只做格式、源码与依赖声明检查，不声称已经验证依赖安装或跨平台运行。
3. Repository Variables：`REVIEW_API_BASE`（如供应商的 HTTPS OpenAI-compatible v1 地址）、
   `REVIEW_MODEL`；Secret：`REVIEW_API_KEY`。CLI 使用通用兼容请求，不预设模型或收费账号。
   当前仓库已配置：百炼兼容接口 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `deepseek-v4-pro-0813`；密钥仅存 GitHub Secret。已通过真实示例审核：模型返回有效 JSON 且完成全部文件阅读校验。
4. 定时发现需要 `MARKET_APP_ID`、`MARKET_APP_PRIVATE_KEY` 对应的 GitHub App，授予市场仓库
   Contents read、Issues read/write。使用 App 创建的 Issue 才能触发后续审核工作流。
   未配置时可以由作者直接提交 Issue，不影响手工申请。
5. 仓库允许 Actions 的批准任务提交 registry/catalog/versions；分支保护需允许指定机器人提交，
   或由维护者调整为受保护的提交方式。失败不会报告上架成功。

实际安装兼容性以运行露比的系统、Python 和包索引为准；市场不以某个审核机器的环境冒充所有部署环境。

## 组件类型自动识别

插件 `_manifest.json` 不需要声明 `comp_types`；历史插件即使保留该字段，也不用于分类或拦截审核。
市场只解析 Python AST，从公共 BaseCommand、BaseTool、BaseEvent 的导入别名与本地继承关系
识别 command、tool、event，不导入或执行插件。结果写入审核报告的 `component_analysis`，
并纳入报告指纹；批准后生成目录条目的 `comp_types`，这是系统生成的展示数据，不是作者声明。
动态工厂、未解析的继承或实例绑定会标记 unknown 并提示人工复核，不因缺少声明而失败。
静态识别不保证组件最终注册或执行成功；加载后以宿主实际注册的组件为准。Hook 是接入位置，不是组件类型。
旧版本记录没有静态识别结果时，重建目录保留未知分类，不回退到作者声明，不改写历史批准记录。
规则变更需重新检查、重新审核后人工批准，旧报告不能直接复用。

## 依赖声明

插件 `_manifest.json` 保留主程序现有格式，额外接受：

```json
{"python_dependencies":[{"name":"httpx","version":">=0.28,<1"}]}
```

作者只填直接第三方运行依赖，即使基础环境已有也填写；标准库不填，间接依赖自动解析。
依赖名与范围不接受 URL、Git 仓库、pip 参数、extras 或任意安装脚本。
插件间依赖继续用 `plugin_dependencies`，与 Python 库不同。
不限制露比最低/最高版本。

依赖按 MaiBot 形式在实际运行环境处理：

- 已安装的库直接复用，不重新下载；版本不满足声明时给出警告，仍尝试加载插件。
- 只安装缺失的直接依赖，安装工具负责解析间接依赖。
- 安装前快照全部已安装版本，作为临时约束，禁止为新依赖升级或降级已有共享库。
- 优先 `uv pip install --python 当前解释器`，无 uv 时使用当前解释器的 pip；沿用主程序配置的索引顺序，逐源重试。
- 不强制 wheel，不锁定统一平台清单；缺失包可按安装工具正常流程构建源码包，构建可能执行第三方构建代码，发生在用户运行环境而非市场审核任务。
- 补装失败记录并展示警告，仍尝试加载插件；最终导入、初始化失败由正常加载与回滚流程处理。
- 已安装的共享库不自动卸载，插件目录与配置回滚不代表 Python 环境回滚。

因此本地已有 `jieba`、`pyexecjs` 不会因缺少 PyPI wheel 而被市场要求重新安装。
完整依赖补装和真实插件的线上运行仍待联调。

## 审核和维护

申请状态标签、中文失败原因及重试方式见 [申请进度与结果反馈](docs/application-feedback.md)。

- 作者通过表单申请；评论指令仅保留维护者的 `/approve` 和 `/reject`。
- AI 源码审核结果只生成中文 JSON 报告，不直接批准。归档内全部文件均送审，包括隐藏文件、无扩展名文件和各种脚本。
  不按扩展名筛选，不静默丢弃目录。不能作为 UTF-8 文本读取、含二进制控制字符或超过额度时停止审核。
  `files_read` 必须覆盖全部送审路径；缺少任何文件即标记 `incomplete`，不能批准，复用旧报告时同样校验。
- 维护者读完机器人报告，单独评论 `/approve`。任务重新下载并验证，匹配已发布报告的指纹及原 AI 报告，
  检查内容变化需重新审阅，不盲目批准后来的源码。
- `/reject` 仅维护者可用，保存拒绝记录；不能重推同名版本绕过。
- 定时扫描为北京时间 02:00、15:00，GitHub 调度可能延迟，支持手动运行。
- 普通版本更新沿用原登记；仓库迁移和维护者转让暂不开放申请，后续再补相应流程。
  同步更新已有版本记录的归属需专门迁移工具；第一版不自动转移。
- 支持维护者通过 Actions 手动下架或恢复整个插件，保留版本及操作历史；详见 [下架与恢复](docs/plugin-withdrawal.md)。不因网络失败删除条目。
- 下架后原仓库可提交高于全部历史记录的修复版，审核期间继续下架；维护者用 `/approve` 明确批准后才重新上架，无需先恢复旧版本。

## 输出与客户端边界

- `registry.json`：唯一插件名和已批准仓库绑定。
- `versions/<name>/<version>.json`：审核记录、批准人、固定源码、依赖声明与运行时策略和 AI 报告。
- `catalog.json`：现有客户端可读的数组，按数字版本号选择最新已批准版本，不提供历史版本选择。
  `dependency_policy` 记录实际环境安装策略；不再生成或要求 `dependency_lock`，旧锁定清单不参与新客户端的依赖决策。
- registry、版本记录、catalog 由同一个 Git commit 发布。并发写入串行化，push 失败不关闭申请。
- 服务器用实际环境约束保护已有共享库，补装失败仅警告并尝试加载；更新由用户点击，不后台静默更新，不做版本锁定。
- 更新按新版结构重建配置：同名用户值保留，新增用默认值，废弃字段删除，改名不自动转换。
  配置重建、依赖自动安装和失败恢复已在露比主程序本地实现，不在市场脚本内执行。
  新配置验证或加载失败时恢复原插件目录、配置及安装记录；共享环境中已安装的 Python 库不回滚。
- 网页端已接入“有更新”、手动安装／更新入口，以及详情页阶段进度、成功结果、失败原因和恢复结果展示。
  下载、校验、安装 Python 依赖、备份准备、重建配置并加载由后端实际阶段驱动；尚待真实市场浏览器联调。

## 检查规则维护

`market/rules/manifest_schema.json` 与 `market/rules/static_checks.py` 是市场自身维护的规则，初始从露比当前实现同步。
工作流直接使用市场仓库检出的规则，不再拉取主程序或执行外部仓库提供的检查脚本。
审核不导入完整露比，也不导入第三方插件。规则文件和校验入口内容指纹写入审核报告，批准时重新校验并匹配已发布报告。
规则变更后必须重新检查、重新审阅，再人工批准；仓库 Git 历史记录规则变更。
`python_dependencies` 已在市场 schema 和当前主程序 schema 中支持。规范变化时需要同步修改两边并验证一致性。

## 暂时停用与后续补充

- 宿主版本范围拦截：主程序加载入口已注释停用，保留字段、校验函数和原调用注释；当前不按 `min_version` / `max_version` 拒绝加载。后续如需启用，先另行确定兼容策略。
- 插件间依赖：市场安装器已注释“声明 `plugin_dependencies` 就拒绝安装”的旧拦截。市场端“依赖已满足则允许、缺失则列出插件名和版本要求、由用户手动安装”的引导暂缓实现。
  运行时依赖图和加载顺序检查仍保留，依赖缺失或版本不符仍可能在加载阶段失败并触发安装回滚；不自动安装依赖插件。
- 以上暂缓项不影响 Python 库的自动安装及冲突校验，也不代表完整兼容策略或依赖引导已经实现。

## 真实 AI 冒烟验证

2026-10-22 本地和 GitHub Actions 真实调用均通过，使用百炼 `deepseek-v4-pro-0813` 审核公开的 `examples/demo_plugin`。
测试不创建申请、不发表评论、不批准或修改正式索引，验证报告保留 `static_only`，不能用作收录凭证。
手动运行 Actions 的 `AI review smoke test` 可复测（会产生模型调用费用）。
本次运行：https://github.com/shengjiezhang1019-creator/lubi-plugin-market/actions/runs/39750078123，报告保存在该次运行的 `ai-review-smoke` artifact（保留 7 天）。
