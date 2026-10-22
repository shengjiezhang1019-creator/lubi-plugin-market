# 露比插件市场

独立于露比主程序的 GitHub 市场项目。目标流程：

```text
作者发布公开 Release → Issue 申请 / 定时发现
→ 固定 commit 与 ZIP SHA256 → 框架静态检查 → 基础环境依赖解析
→ AI 阅读源码生成报告 → 维护者 /approve → 写入 registry、versions、catalog
```

AI 不批准、不执行插件。首次和每次版本更新都由具有市场仓库 write/admin 权限的维护者批准。
当前是本地第一版，尚未创建远程仓库或配置真实发布环境，不等于线上市场已经可用。

## 本地开始

```powershell
python -m pip install -e '.[dev]'
python -m pytest -q
python -m market.cli demo --host C:\AGI --work .work/demo
```

demo 不联网、不调用 AI、不安装依赖，只验证测试源码的结构与实际主程序静态规则。
报告会标记 `static_only`，不能进入批准流程；不会给 catalog 添加虚构插件。

完整远程验证（先完成下面的配置）：

```powershell
python -m market.cli validate --host PATH_TO_FIXED_HOST_CHECKOUT --request request.json --work .work/real
python -m market.cli ai --work .work/real
```

request.json：

```json
{"plugin_name":"weather_plugin","repository":"https://github.com/OWNER/weather-plugin","tag":"v1.0.0"}
```

## 需要维护者配置的真实环境

1. `config/market.json` 的 `host_repository` 填可访问的主程序 GitHub 仓库或 GitHub 镜像，
   `host_commit` 填固定的 40 位 commit。当前主程序在 GitLab，不擅自上传或虚构其 GitHub 镜像。
   非公开工具仓库需要只读 `HOST_READ_TOKEN`。检查不读取待审仓库提供的脚本。
2. 为 `config/baselines/linux-py311.txt` 提供实际服务器的完整固定基础依赖清单。
   标准环境版本应按实际部署确定；当前示范 profile 为 Ubuntu 24.04 + Python 3.11。
   正式流程缺少配置时停止，不能把 demo 当作完整审核。
3. Repository Variables：`REVIEW_API_BASE`（如供应商的 HTTPS OpenAI-compatible v1 地址）、
   `REVIEW_MODEL`；Secret：`REVIEW_API_KEY`。CLI 使用通用兼容请求，不预设模型或收费账号。
4. 定时发现需要 `MARKET_APP_ID`、`MARKET_APP_PRIVATE_KEY` 对应的 GitHub App，授予市场仓库
   Contents read、Issues read/write。使用 App 创建的 Issue 才能触发后续审核工作流。
   未配置时可以由作者直接提交 Issue，不影响手工申请。
5. 仓库允许 Actions 的批准任务提交 registry/catalog/versions；分支保护需允许指定机器人提交，
   或由维护者调整为受保护的提交方式。失败不会报告上架成功。

不同操作系统、Python 版本必须有独立验证及依赖清单；当前仅实现一个 profile，不声称跨平台支持。

## 依赖声明

插件 `_manifest.json` 保留主程序现有格式，额外接受：

```json
{"python_dependencies":[{"name":"httpx","version":">=0.28,<1"}]}
```

作者只填直接第三方运行依赖，即使基础环境已有也填写；标准库不填，间接依赖自动解析。
依赖名与范围不接受 URL、Git 仓库、pip 参数、extras 或任意安装脚本。
插件间依赖继续用 `plugin_dependencies`，与 Python 库不同。
不限制露比最低/最高版本。

市场将基线和插件依赖共同解析，生成包含具体版本、wheel 文件名、SHA256 的完整清单。
第一版仅接受 PyPI 上适配环境的 wheel，不在审核或生产中构建未知源码包。
检查包括解析下载、临时目录按哈希安装 wheel、核对安装后元数据；不执行 `.pth` 或导入这些库。
**这不是插件运行测试**，不保证外部服务、系统动态库或插件功能正常。
服务器自动安装已在露比主程序本地实现并通过离线测试：消费市场锁定清单、校验 wheel 与哈希，按需补装缺失库，冲突时拒绝升级或降级已有共享库。
真实 GitHub 市场与部署环境的完整联调尚未完成，不能仅部署本仓库就宣称线上功能已经可用。

## 审核和维护

- 作者通过表单申请，`/recheck` 仅申请作者或市场维护者可用。
- AI 源码审核结果只生成中文 JSON 报告，不直接批准。归档内全部文件均送审，包括隐藏文件、无扩展名文件和各种脚本。
  不按扩展名筛选，不静默丢弃目录。不能作为 UTF-8 文本读取、含二进制控制字符或超过额度时停止审核。
  `files_read` 必须覆盖全部送审路径；缺少任何文件即标记 `incomplete`，不能批准，复用旧报告时同样校验。
- 维护者读完机器人报告，单独评论 `/approve`。任务重新下载并验证，匹配已发布报告的指纹及原 AI 报告，
  检查内容变化需重新审阅，不盲目批准后来的源码。
- `/reject 原因` 仅维护者可用，保存拒绝记录；不能重推同名版本绕过。
- 定时扫描为北京时间 02:00、15:00，GitHub 调度可能延迟，支持手动运行。
- 普通版本更新沿用原登记；仓库迁移和维护者转让使用修改登记表单，**当前需维护者核验后手工修改 registry**。
  同步更新已有版本记录的归属需专门迁移工具；第一版不自动转移。
- 撤回、下架后的产品处理暂不实现；不因网络失败删除条目。

## 输出与客户端边界

- `registry.json`：唯一插件名和已批准仓库绑定。
- `versions/<name>/<version>.json`：审核记录、批准人、固定源码、依赖清单和 AI 报告。
- `catalog.json`：现有客户端可读的数组，按数字版本号选择最新已批准版本，不提供历史版本选择。
  `dependency_lock` 已由当前主程序安装器读取；旧客户端会忽略字段，不会自动安装库。
- registry、版本记录、catalog 由同一个 Git commit 发布。并发写入串行化，push 失败不关闭申请。
- 服务器遇到依赖冲突不自动升级/降级共享库；更新由用户点击，不后台静默更新，不做版本锁定。
- 更新按新版结构重建配置：同名用户值保留，新增用默认值，废弃字段删除，改名不自动转换。
  配置重建、依赖自动安装和失败恢复已在露比主程序本地实现，不在市场脚本内执行。
  新配置验证或加载失败时恢复原插件目录、配置及安装记录；共享环境中已安装的 Python 库不回滚。
- 网页端已接入“有更新”、手动安装／更新入口，以及详情页阶段进度、成功结果、失败原因和恢复结果展示。
  下载、校验、安装 Python 依赖、备份准备、重建配置并加载由后端实际阶段驱动；尚待真实市场浏览器联调。

## 检查工具复用

从固定主程序 checkout 读取原始 manifest schema，并抽取 `market/validation.py` 中的纯 AST 检查函数。
不导入完整露比，也不导入第三方插件，避免配置、数据库和动态注册检查的副作用。
工具 commit 或检查文件脏改不匹配时停止；未来主程序检查函数变更需明确适配，不静默降级。
`python_dependencies` 已同步到当前主程序 schema 与 manifest 校验。上线选择的固定工具 commit 必须包含这些更新，不能只填写旧版 HEAD。

## 暂时停用与后续补充

- 宿主版本范围拦截：主程序加载入口已注释停用，保留字段、校验函数和原调用注释；当前不按 `min_version` / `max_version` 拒绝加载。后续如需启用，先另行确定兼容策略。
- 插件间依赖：市场安装器已注释“声明 `plugin_dependencies` 就拒绝安装”的旧拦截。市场端“依赖已满足则允许、缺失则列出插件名和版本要求、由用户手动安装”的引导暂缓实现。
  运行时依赖图和加载顺序检查仍保留，依赖缺失或版本不符仍可能在加载阶段失败并触发安装回滚；不自动安装依赖插件。
- 以上暂缓项不影响 Python 库的自动安装及冲突校验，也不代表完整兼容策略或依赖引导已经实现。
