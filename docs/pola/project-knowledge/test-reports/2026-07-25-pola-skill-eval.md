# 测试报告：pola-skill-eval

artifact: test-evidence

## 结论

**Pass。** Skill 规范、静态门禁、suite 合约、确定性 grader、baseline/candidate 配对执行、故障隔离、性能统计和自评均通过。测试完全离线，不访问真实 Agent、用户数据或凭证。

## 验收摘要

| 验收 | 证据 | 结果 |
| --- | --- | --- |
| A1–A2 | `quick_validate.py`、225 行 `SKILL.md`、本地引用自检 | Pass |
| A3 | good/bad fixture、符号链接、非法 UTF-8、危险命令、脚本语法 | Pass |
| A4 | release coverage、重复 ID、未知 grader、路径逃逸、case 上限 | Pass |
| A5–A6 | dry-run、26-run matrix、唯一 workspace、cwd/输出边界、超时、输出上限 | Pass |
| A7 | JSON/Markdown、硬门禁、逐 run 证据、uplift、trigger F1、p50/p95 | Pass |
| A8 | 30 次静态性能探针和小于 15 秒的端到端预算 | Pass |
| A9 | Skill 校验、Python 编译、20 项单测和 harness | Pass |
| A10 | 干净发布分支的 JSON、`git diff --cached --check`、secret 和缓存扫描 | Pass |
| A11 | 基于最新 main 的独立分支，只发布目标子目录和匹配工程记录 | Pending publish |
| A12 | Requirement、PRD、SDD、测试矩阵、本报告和开发日志齐备 | Pass |

完整测试矩阵：`delivery/pola-skill-eval/function_test_cases.json`

## 离线 Harness

```text
python3 pola-skill-eval/scripts/run_harness.py
结果：20 tests，全部通过；harness passed=true
```

配对集成结果：

- 评测模式：`release`
- case：6
- baseline/candidate runs：26
- candidate success：`1.0`
- baseline success：`0.307692`
- uplift：`0.692308`
- candidate trigger F1：`1.0`
- candidate p95：`47.275 ms`
- 端到端集成耗时：`0.615 s`，预算 `15 s`

性能探针：

- 静态检查样本：30
- p50：`0.554 ms`，预算 `150 ms`
- p95：`0.838 ms`，预算 `500 ms`
- Skill 自评：`pass`
- 自评 findings：0
- 近似常驻正文：2219 tokens

这些数字来自 fake Agent 离线 fixture，用于验证 evaluator 的决策、统计和性能回归机制，不代表真实模型平台的绝对时延。

## CLI Smoke

```text
validate_suite.py --mode release
结果：valid=true，6 cases，0 warnings

run_matrix.py（不带 --execute）
结果：只输出计划，未创建 output

run_matrix.py --execute
结果：pass / high confidence
```

## 健壮性与安全证据

- 错误 Skill 同时暴露缺少 description、非法名称、缺引用、Python 语法错误和宽泛递归删除。
- 外部符号链接和 grader `..` 路径在执行前拒绝。
- 非 UTF-8 `SKILL.md` 返回结构化 reject，不使检查器崩溃。
- 1 秒超时会终止 fake runner；100 KB stdout 在 1 KiB 上限下终止。
- 敏感 runner env 名称、候选内部输出目录、越界 cwd 被拒绝。
- runner 不使用 shell；每个 job 有隔离 workspace。
- suite 最多 200 case，matrix 最多 5000 job；并发、timeout、prompt、grader、argv、env 和输出均有限制。
- aggregate report 不复制原始 response/stdout；原始证据只在显式输出目录。

## 不影响功能使用

- 新增独立 `pola-skill-eval/`，未修改已有 Skill 的入口、API、数据或配置。
- 不涉及 UI，Browser 截图不适用。
- 没有生产任务、网络请求、消息外发、定时任务、服务重启或 secret 轮换。
- 未在本地脏工作树暂存或覆盖任何既有项目文件。

## 残余风险

- 进程限制不是操作系统 sandbox；执行未知生成代码仍应放在容器、VM 或受限账户。
- 静态模式扫描不能证明没有混淆或间接恶意行为。
- fake Agent 能证明 harness 正确性，真实宿主仍需实现并验证 runner adapter。
- 小样本的真实模型评测可能有随机性，应使用 holdout 和至少三次 trial。
