# 测试报告：pola-wechat-public-account-reader V3 私人宽松模式

artifact: test-evidence

## 测试结论

**Pass。** 显式 `private_opt_in`、机器之心三来源、审计报告、未来时间异常、敏感参数、失败隔离和
V2 兼容路径通过。该模式放开来源许可引用门禁，不绕过服务端 Token、CAPTCHA、登录页或正文质量
门禁。

## 验收矩阵

| 验收 | 证据 | 结果 |
| --- | --- | --- |
| A1 | V3 Requirement、PRD、SDD、测试矩阵、测试报告、开发日志及本地链接测试 | Pass |
| A2–A5 | 策略枚举、严格布尔、显式来源/正文开关、停用 target/source 回归 | Pass |
| A6–A8 | 机器之心私人资产、Token 隔离、作者/`biz`/唯一微信原文和正文质量门禁 | Pass |
| A9–A10 | validate、JSON、Markdown 三个审计清单和 cron 参数 | Pass |
| A11 | SSRF、DNS pin、Cookie、secret、跨域鉴权重定向、容量、锁和未来时间门禁 | Pass |
| A12 | 旧来源/SQLite、四宿主安装器、Codex 实机安装路径 | Pass |

完整矩阵：

- `delivery/pola-wechat-public-account-reader/function_test_cases_v3.json`

## 离线验证

```text
python3 -B pola-wechat-public-account-reader/scripts/run_harness.py
结果：81 tests，全部通过，OFFLINE_HARNESS_OK
```

新增重点：

- `private_opt_in` 只启用 `enable_in_private_mode=true` 的 source；停用 target 和普通停用 source
  不受影响。
- 停用 source 不会因 `fetch_content_in_private_mode` 被误记为私人正文来源。
- `password`、`client_secret`、`client-secret`、`access-token`、`authToken`、`jwt` 等敏感 query
  在配置 URL 和候选 URL 两条路径均联网前失败。
- `max_future_hours` 对全部 source 严格校验；机器之心 xInfinite 使用一小时容差。全是未来异常
  条目时为 `source_unavailable/source_entries_rejected`，不误报为零更新。
- Markdown 明确列出私人启用来源、私人正文来源和 permission override 的 target/source 标识。
- 机器之心资产固定校验 target `biz`、嵌入微信原文模式、预期作者和未来时间容差。

## 公网验证

### 标准来源 smoke

```text
python3 -B pola-wechat-public-account-reader/scripts/run_harness.py --live
结果：live_status=pass
```

- 微信 Album：20 条，作者身份与目标 `biz` 匹配。
- 微信 Homepage：20 条，作者身份与目标 `biz` 匹配。
- 机器之心官网 sitemap：20 条，URL 和显式日期有效。
- 抽样微信正文：`blocked/captcha`，未进入摘要。

### 私人 RSS smoke

```text
python3 -B pola-wechat-public-account-reader/scripts/run_harness.py --live-private
结果：live_private_status=pass
```

- 官方 RSS：`source_unavailable/missing_secret`，符合未设置 Token 的预期。
- xInfinite：11 条当前有效文章；另有 19 条因身份、URL 或未来时间门禁拒绝。
- 抽样微信正文：`blocked/captcha`；没有把验证码当正文或摘要。

### 机器之心滚动 720 小时 dry run

运行 ID：`20260725T031449Z-725e23`。

- `run_status=degraded`、`coverage=partial`。
- 官网 sitemap：343 条，`status=ok`。
- 官方 RSS：0 条，`missing_secret`。
- xInfinite：11 条，`status=ok`；19 条被拒绝。
- 合并候选：354 条；dry run 无 checkpoint，因此“new”不代表历史增量。
- 来源摘要：11 条，全部 `summary_basis=source_summary`。
- 正文：3 条尝试均被 `publisher_data_service_gate` 阻断，可信全文 0。
- 时间范围：2026-06-26T00:00:00Z 至 2026-07-24T23:32:26Z；修复前观察到的未来时间条目未进入结果。

证据：运行 ID `20260725T031449Z-725e23` 的 JSON/Markdown 报告已在测试时核验；临时运行目录
未纳入 Git。

公网数量随上游变化，不是固定验收常量。三来源仍不能证明微信公众号近 30 天完整清单。

## 工程与安装态门禁

```text
quick_validate.py pola-wechat-public-account-reader
结果：Skill is valid!

validate_function_test_cases.py ...
结果：PASS，覆盖 12 个验收项、6 个 feature、14 个 case

validate_pola_skills.py
结果：PASS: Pola skill harness found no issues.
```

- JSON、Python 语法、本地 Markdown 链接、尾随空白和定向 secret 扫描通过。
- 规范路径和 Codex 安装路径分别运行 81 项离线 Harness，均通过。
- Codex 链接解析到 `$REPO_ROOT/pola-wechat-public-account-reader`。
- 独立安全终审结论：Pass，无 P0/P1/P2/P3 blocker。

## 集成与稳定性回归

- 旧 Album、Homepage、RSS/Atom、sitemap、article URL、JSON API 和 SQLite 保持。
- `version=1` 配置和状态库不迁移；新字段有兼容缺省值。
- 请求、运行、响应、解压、分页、候选、正文、锁和保留期上限保持。
- cron 只生成示例，不自动安装；无生产重启、外部通知、密钥轮换或数据清理。
- 无 UI 变更，不需要 Browser 截图。
- 四宿主安装器在临时 HOME 的检查、安装、幂等和冲突拒绝通过；Codex 实机链接通过。Claude
  Code、Qoder 和 Qoder Work 未安装到真实用户目录，也不宣称完成 runtime smoke。

## 残余风险

- 官方 RSS 仍需要真实 `MACHINEHEART_RSS_TOKEN`。
- 免费公网来源和第三方 feed 均为 `partial`，结构、时钟和反爬状态会变化。
- xInfinite 摘要是来源摘要，不是已读取全文摘要。
- Qoder/Qoder Work 的符号链接发现需要在对应运行时另行 smoke。
