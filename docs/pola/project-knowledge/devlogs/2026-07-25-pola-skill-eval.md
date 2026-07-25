# 开发日志：pola-skill-eval

artifact: finalization

## 目标和风险

新增一个评测其他 Agent Skill 的 Skill，重点验证可用性、健壮性和执行效率，并提供可复现 harness。

风险等级：**P2**。动态路径会执行用户提供的 runner；主要风险为未知代码、无限执行、超大输出、路径逃逸、环境 secret 继承和伪阳性。

## 核心决策

- 使用“薄 Skill + Python 标准库确定性引擎 + argv runner adapter”。
- 把静态硬门禁、动态任务 lift、触发质量、健壮性和性能证据分开。
- `baseline/candidate/old` 同题执行，candidate 必须证明相对 baseline 有提升。
- 动态执行默认关闭；只有 `--execute` 才启动 runner。
- 不用一个总分覆盖 critical 失败；输出五种明确决策状态。
- 发布前复查发现目标 GitHub 已有并发提交，因此基于最新 `main` 创建独立分支和 PR，只发布目标子目录及匹配工程记录。

## 实现

- `SKILL.md`：quick/standard/release 工作流、评测方法、执行边界、性能规则和报告要求。
- `agents/openai.yaml`：Codex UI 元数据和默认提示。
- `inspection.py`：元数据、引用、语法、符号链接、风险模式、包体积、上下文估算和五维摘要。
- `suite.py`：release 覆盖、case/grader/阈值/路径/容量合约。
- `runner.py`：dry-run、argv 执行、新鲜 workspace、最小环境、cwd 边界、超时、流式输出上限、并发和单 job 故障隔离。
- `grading.py`：11 类确定性 grader。
- `statistics.py`：成功率、pass@k、pass^k、触发混淆矩阵、p50/p95、token/cost/tool 统计和 baseline/old delta。
- `reporting.py`：稳定 JSON 和 Markdown 报告。
- `references/`：方法论、suite、runner、安全策略和 JSON schema。
- `tests/`：good/bad template、fake Agent、20 项单测和 26-run 集成 harness。

## 验证

- Skill `quick_validate.py`：Pass。
- Python `compileall`：Pass。
- 离线单元/健壮性测试：20/20 Pass。
- 配对集成：26 runs，candidate `1.0`、baseline `0.307692`、uplift `0.692308`、trigger F1 `1.0`。
- 性能：干净发布分支静态 30 次 p50 `0.554 ms`、p95 `0.838 ms`；集成 `0.615 s`；预算均通过。
- 自评：`pass`，0 findings，近似 2219 个正文 token。
- release suite CLI、dry-run 无副作用和 execute smoke：Pass。
- 详细证据：`test-reports/2026-07-25-pola-skill-eval.md`。

## 稳定性与安全门禁

- 并发最大 8，单次超时最大 600 秒，单次输出最大 10 MiB。
- suite 最大 200 case，展开最大 5000 job；prompt、grader、argv 和 env 都有限制。
- 禁止 shell，拒绝敏感 env 名称、路径逃逸、越界 cwd 和候选目录内部输出。
- 报告只保留布尔/数值 trace 和 grader 证据，不复制原始输出内容。
- 未执行生产任务、访问公网、发送消息、安装 cron、修改服务、处理真实数据或使用凭证。
- 无 UI 变更，截图不适用。

## Git 与外部同步

- 源 `PolaSkills` 工作树存在大量用户既有无关改动；本次不会暂存或提交它们。
- GitHub 目标：`PolarisW007/PolaSkills`。发布使用独立干净临时 clone，只复制并提交 `pola-skill-eval/` 与本次 Requirement、PRD、SDD、测试矩阵、测试报告、开发日志和索引事实。
- 实现 commit：`098b540`。
- Draft PR：`https://github.com/PolarisW007/PolaSkills/pull/2`。
- 远端分支：`agent/pola-skill-eval`，基于最新 `main`。
- 未同步钉钉或其它外部系统；用户没有授权该类外部写入，保留为未执行项。

## 回滚

远端对初始提交执行 Git revert，或删除独立 `pola-skill-eval/` 后提交。没有数据库、配置或状态迁移。
