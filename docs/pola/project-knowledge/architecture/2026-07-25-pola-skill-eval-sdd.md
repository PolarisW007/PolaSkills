# pola-skill-eval SDD

## 架构决策

采用“薄 Skill 编排层 + Python 标准库确定性评测引擎 + 可插拔 argv runner”。

### 备选方案

1. **纯 LLM rubric**
   - 优点：实现快、能判断开放式质量。
   - 缺点：不可复现、容易自证、无法可靠测超时和性能。
   - 结论：仅允许作为补充 grader 的外部证据，不作为核心门禁。

2. **单一大脚本**
   - 优点：文件少。
   - 缺点：结构检查、套件、执行、评分和报告耦合，难以独立测试。
   - 结论：不采用。

3. **模块化标准库引擎**
   - 优点：无需安装依赖，可单测，可替换 runner，可在 CI 中稳定运行。
   - 缺点：JSON/YAML 子集和 schema 校验需要自行实现。
   - 结论：采用。

## 目录结构

```text
pola-skill-eval/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── inspect_skill.py
│   ├── validate_suite.py
│   ├── run_matrix.py
│   ├── run_harness.py
│   └── pola_skill_eval/
│       ├── __init__.py
│       ├── grading.py
│       ├── inspection.py
│       ├── reporting.py
│       ├── runner.py
│       ├── statistics.py
│       ├── suite.py
│       └── utils.py
├── references/
│   ├── eval-report.schema.json
│   ├── eval-suite.schema.json
│   ├── evaluation-method.md
│   ├── runner-adapters.md
│   ├── security-policy.md
│   └── suite-format.md
├── evals/
│   └── evals.json
└── tests/
    ├── fake_agent.py
    ├── fixtures/
    │   ├── bad-skill-template/
    │   ├── good-skill/
    │   ├── runner.json
    │   └── suite.json
    └── test_*.py
```

Skill 内不增加 README；渐进披露由 `SKILL.md` 路由到 `references/`。

## 模块职责

### `inspection.py`

- 读取并校验 `SKILL.md` frontmatter。
- 支持 `codex`、`agent-skills`、`generic` 和自动 profile。
- 盘点普通文件、目录、总字节、符号链接和特殊文件。
- 检查 Markdown 本地引用、路径越界和缺失引用。
- 对 Python 使用 `ast.parse`，对 shell 使用 `bash -n`。
- 扫描 critical/high/medium 风险模式。
- 计算正文行数、近似 token、Skill 总体积和检查耗时。

### `suite.py`

- 加载和校验评测套件 JSON。
- 校验唯一 case ID、类别、split、trial、超时、输出上限和 grader。
- 拒绝绝对路径与 `..` grader 路径。
- release 模式强制要求核心覆盖和至少一个确定性 grader。

### `runner.py`

- 读取 argv 数组 runner 模板，展开受控占位符。
- 为每个 case/arm/trial 创建独立工作区。
- 使用 `subprocess.Popen(..., shell=False)`。
- 只传入环境白名单与明确配置的非敏感变量。
- 并行度最大为 8。
- 流式读取 stdout/stderr，超过输出上限时终止进程。
- 超时时先 terminate，再 kill。
- 保存截断日志、响应、trace 和 `run.json`。

### `grading.py`

- 执行内置确定性 grader。
- 每个 grader 返回 `passed`、`severity`、`expected`、`actual` 和证据。
- critical grader 失败直接触发 case 硬门禁。
- Skill 触发通过 runner trace 的 `skill_invoked` 信号判断。

### `statistics.py`

- 计算成功率、pass@k、pass^k、触发混淆矩阵和 precision/recall/F1。
- 计算 duration、output bytes、token、cost、tool calls 的 p50/p95。
- 对缺失或样本量不足的指标显式标记，不做推断填充。

### `reporting.py`

- 生成稳定字段顺序的 JSON 报告。
- 生成 Markdown 摘要、硬门禁、arm 对比、逐 case 结果和 findings。
- 决策逻辑不把软分数用于覆盖硬门禁。

## 数据契约

### Suite

顶层包含：

- `schema_version`
- `name`
- `mode`
- `defaults`
- `cases`

case 包含：

- `id`
- `category`
- `split`
- `prompt`
- `should_trigger`
- `trials`
- `timeout_seconds`
- `max_output_bytes`
- `graders`

### Runner

包含：

- `argv`：字符串数组；支持 `{skill_path}`、`{prompt_file}`、`{workspace}`、`{response_file}`、`{trace_file}`、`{arm}`、`{case_id}`、`{trial}`。
- `cwd`：可选，必须存在。
- `env`：可选；敏感名称拒绝。

### Trace

runner 可选写入：

```json
{
  "skill_invoked": true,
  "tool_calls": 2,
  "usage": {
    "input_tokens": 100,
    "output_tokens": 40,
    "cost_usd": 0.001
  }
}
```

缺失 trace 不导致确定性任务 grader 失败，但触发与成本指标可能变为 `inconclusive`。

## 决策算法

1. 静态 critical 或结构硬门禁失败：`reject`。
2. 配置/环境无法执行：`blocked`。
3. 动态执行没有足够已完成样本：`inconclusive`。
4. 任一 critical grader 失败、触发反例明显误触发或关键性能门禁失败：`reject`。
5. 关键用例通过但有 high finding、候选未显著优于 baseline 或样本量偏小：`conditional`。
6. 所有硬门禁通过、candidate 关键成功率满足阈值且相对 baseline 有正向提升：`pass`。

## 性能护栏

- `ThreadPoolExecutor(max_workers=min(requested, 8))`。
- 单个 suite 最多 200 个 case，展开后的 matrix 最多 5000 个 job。
- 单 case 最多 50 个 grader，prompt 最多 1 MiB。
- runner 最多 128 个 argv 和 32 个非敏感环境变量，并限制单值长度。
- 每个 subprocess 有绝对超时。
- stdout/stderr 读取有累计字节上限。
- 只读取统计所需的 Skill 文件；单文件大小超过阈值时不全量载入。
- 报告保留聚合指标，日志按上限截断。
- 自身 harness 记录静态检查 p50/p95 和端到端总耗时。
- 不把 concurrency 当作吞吐结论；报告同时保留样本数和运行限制。

## 安全边界

- 禁止 shell 拼接。
- 拒绝敏感 runner env 名称。
- 不解析或执行被评测 Skill 的指令文本。
- 静态扫描不会跟随越界符号链接。
- 动态执行只由显式 `--execute` 开启。
- 运行目录由 harness 创建，输出目录默认不可覆盖。
- runner `cwd` 必须解析到当前 job 的新鲜 workspace 内。
- 没有操作系统级 sandbox 承诺；评测未知代码时仍建议由调用方在容器或受限账户运行。

## 测试策略

### 单元测试

- 合格/不合格 Skill 静态检查。
- frontmatter profile 和引用。
- suite 正常路径与各类非法输入。
- grader 正反路径与路径逃逸。
- 统计和报告状态。

### 集成测试

- fake agent 运行 baseline/candidate。
- 验证 candidate 的核心任务和恢复任务优于 baseline。
- 验证不该触发的 case 不触发。
- 验证 dry-run 不启动 runner。
- 验证超时和输出上限。
- 验证每次 trial 的 workspace 唯一。

### 性能测试

- 对合格 fixture 重复静态检查，记录 p50/p95。
- 运行完整离线 matrix，检查总耗时预算。
- 验证报告包含 duration/output bytes；trace 存在时包含 token/tool/cost。

## 部署与回滚

本次没有运行时服务、数据库或生产配置。部署等价于 GitHub 子目录发布。

- 发布前：quick validate、编译、单测、harness、diff、secret scan。
- 发布：基于最新 `main` 的独立分支，只加入 `pola-skill-eval/` 与本次 Pola 工程记录。
- 发布后：通过 GitHub tree 核对顶层只有预期子目录，复跑公开目录中的 harness。
- 回滚：Git revert 初始提交或删除该子目录并提交。

## 迁移与兼容

这是新增独立 Skill，无数据迁移。现有 Skills、入口和脚本保持不变。runner 是显式适配器，不假设 Codex、Claude 或其它宿主内部 CLI，因此可由不同 Agent 平台实现同一契约。
