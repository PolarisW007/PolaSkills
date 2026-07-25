# pola-skill-eval PRD

## 产品定义

`pola-skill-eval` 是一个“评测其他 Skill 的 Skill”。它指导 Agent 先建立可复现的评测契约，再调用确定性脚本检查候选 Skill、执行基线对照、汇总质量与性能证据，最后给出可审计的发布建议。

## 核心原则

- **先证明有效，再讨论分数**：候选 Skill 必须相对 baseline 改善真实任务结果。
- **硬门禁优先**：结构错误、安全风险、关键用例失败不能被平均分稀释。
- **同题同环境**：各评测臂使用相同 case 和限制，但工作区互相隔离。
- **确定性优先**：能用文件、退出码、JSON、耗时验证的，不交给主观模型裁判。
- **不确定就如实报告**：样本不足、runner 不可用或 trace 缺失时输出 `inconclusive`，不伪装成通过。
- **性能必须有基线**：耗时、token 或工具调用增加只有在质量提升足够时才合理。

## 用户流程

### Quick：静态预检

1. 用户给出候选 Skill 目录。
2. Skill 调用静态检查器。
3. 检查元数据、结构、引用、脚本语法、风险模式和体积。
4. 输出 JSON/Markdown 发现列表。
5. critical 门禁失败时停止动态执行。

适用于刚生成 Skill 后的快速反馈。

### Standard：配对功能评测

1. 用户准备评测套件和 runner 配置。
2. 校验 case、grader、预算和输出路径。
3. 默认先显示执行计划；用户明确要求执行后传入 `--execute`。
4. 对 `baseline` 与 `candidate` 执行相同 case/trial。
5. 运行确定性 grader，汇总任务成功和触发质量。
6. 比较候选相对基线提升，生成报告。

适用于本地验收和日常迭代。

### Release：发布门禁

在 Standard 基础上增加：

- 可选 `old` 版本回归臂。
- 触发正例、触发反例、核心任务、健壮性、安全、性能覆盖检查。
- 多 trial 统计、p50/p95 和置信度提示。
- 对关键门禁使用更严格判定。
- 建议使用未参与 Skill 编写的 holdout case。

适用于提交或发布前。

## 输入

### 必填

- 候选 Skill 路径。

### 动态评测时必填

- 评测套件 JSON。
- runner 配置 JSON。
- 输出目录。

### 可选

- 旧版本 Skill 路径。
- 指定 profile：`auto`、`codex`、`agent-skills`、`generic`。
- 并发、trial、超时和输出预算。

## 输出

### 静态报告

- Skill 身份和 inventory。
- 硬门禁结果。
- 按 severity 分类的 findings。
- 可用性、健壮性、效率、维护性、安全维度摘要。
- 上下文估算、文件数、字节数和检查耗时。

### 动态报告

- 每个 arm/case/trial 的状态、grader 证据和运行产物位置。
- 各 arm 成功率。
- candidate 相对 baseline/old 的提升。
- 触发混淆矩阵、precision、recall、F1。
- p50/p95、输出体积，以及 trace 可用时的 token、成本、工具调用。
- `pass`、`conditional`、`inconclusive`、`blocked` 或 `reject`。

## 状态与异常分支

- 候选目录不存在：`blocked`，不创建输出。
- `SKILL.md` 不合法：`reject`，跳过动态执行。
- 套件不合法：`blocked`，列出精确 JSON 路径。
- runner 配置不合法：`blocked`，不得尝试 shell 降级。
- 没有 `--execute`：输出经过校验的 dry-run 计划，退出成功。
- runner 超时：终止进程，case 标记 `timeout`，保留截断日志。
- runner 输出超限：终止进程，case 标记 `output_limit`。
- grader 路径逃逸：套件校验失败。
- trace 缺失：继续确定性评分，但相应 token/成本/tool 指标显示为不可用。
- 样本量太小：输出 `conditional` 或 `inconclusive`，不声称统计稳定。
- 输出目录已存在：默认拒绝，防止覆盖证据；需显式选择新的目录。

## 权限与安全体验

- 评测 Skill 只读候选 Skill。
- dynamic harness 是唯一会执行外部命令的路径，必须由用户明确要求。
- 不读取浏览器、SSH、云凭证或任意用户配置目录。
- 不把完整环境传给 runner。
- 报告只记录指标和截断后的日志，不记录敏感变量。
- 本 Skill 不具备发布或消息外发能力；Git 操作由交付流程另行明确执行。

## 可用性要求

- 常见路径有可复制命令示例。
- 失败信息包含问题位置、原因和下一步修复建议。
- JSON 适合 CI，Markdown 适合人工评审。
- quick 模式无需写 runner 或联网。
- fake runner 和 fixtures 让维护者能离线验证整个 harness。

## 非功能要求

- Python 3 标准库，不需要安装第三方包。
- 单次静态检查在测试 fixture 上 p95 小于 500 ms。
- 自测试端到端 harness 在普通开发机上目标小于 15 秒。
- 最大并发 8、最大单次超时 600 秒、最大单次输出 10 MiB。
- 输出顺序稳定，报告字段和退出码可用于 CI。

## 验收体验

一个第一次使用的 Agent 应能：

1. 先运行 quick 检查并理解失败原因。
2. 从参考文件复制最小 suite 和 runner 配置。
3. 在未传 `--execute` 时安全地预览计划。
4. 显式执行后看到 candidate 是否真的优于 baseline。
5. 从报告定位功能、健壮性或性能退化的具体 case。
