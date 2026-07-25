# 测试报告：pola-wechat-public-account-reader V2

artifact: test-evidence

## 测试结论

**Pass。** 批量目标、机器之心公网案例、四宿主目录兼容、失败真实性、正文预算和安全门禁均通过。

本次没有创建生产 cron、发送外部通知、消费商业 API，也没有启用缺少 Agent 接入权益的 RSS 或
条款禁止自动监控的第三方来源。

## 验收矩阵

| 验收 | 风险 | 证据 | 结果 |
| --- | --- | --- | --- |
| V2-A1/A10 | 文档与案例 | Requirement、PRD、SDD、能力/案例、quick validator、本地链接 | Pass |
| V2-A2/A3 | 批量配置和筛选 | 严格 bool/容量/重复 key、target/exclude/priority | Pass |
| V2-A4 | 隔离和公平性 | 三目标局部失败、critical 优先、同级轮询、逐目标/全局预算 | Pass |
| V2-A5/A6 | 机器之心和授权 RSS | 官方 sitemap、env query、嵌入原文 fixture、biz/作者校验 | Pass |
| V2-A7/A8 | 安全和报告语义 | 公网 URL、secret、schema drift、Markdown、coverage_incomplete | Pass |
| V2-A9 | 四宿主目录兼容 | 临时 HOME 四目录安装测试、Codex 实机断链修复和安装路径回归 | Pass |
| V2-A11/A12 | 旧功能与 Harness | 70 项离线测试、三类公网 source smoke、SQLite/cron 回归 | Pass |

完整功能用例矩阵：

- `delivery/pola-wechat-public-account-reader/function_test_cases.json`

## 已运行验证

### 离线 Harness

```text
python3 -B pola-wechat-public-account-reader/scripts/run_harness.py
结果：70 tests，全部通过，OFFLINE_HARNESS_OK
```

覆盖：

- 旧 Album、Homepage、普通 RSS/Atom、article URL、JSON API 和 SQLite 路径。
- 多 target 选择、局部失败隔离、幂等、critical 优先及同优先级轮询。
- 官方 gzip sitemap 有界解压、日期筛选、双前缀修复和候选上限。
- 授权 RSS description 中唯一微信原文、作者和 `biz` 校验。
- 非 Feed XML、非 `urlset` sitemap、缺失 JSON `list_path`、当前窗口空 URL 和拒绝条目不能误报
  `observed_zero`。
- 非法 hostname、credentials、私网 URL、敏感 query、公网 IPv6 canonicalization。
- 只有 `mp.weixin.qq.com` 可形成 `verified` 身份和 `wx:` 去重键。
- HTTP 401/403/404/429/500 的脱敏异常不保留带 Token 的 cause/context。
- Markdown 标题、摘要、netloc、path 和 query 分隔符注入防护。
- 四宿主默认只读检查、显式安装、冲突拒绝和精确断链修复。

### 公网 Harness

```text
python3 -B pola-wechat-public-account-reader/scripts/run_harness.py --live
结果：退出码 0，live_status=pass
```

最终 smoke：

- 微信 Album：20 条，`status=ok`，全部 `biz` 匹配。
- 微信 Homepage：20 条，`status=ok`，全部 `biz` 匹配。
- 机器之心官方 sitemap：20 条，`status=ok`，官网 URL 与显式日期有效。
- 抽样微信正文：`content_status=blocked`、`content_reason=captcha`；验证码未进入摘要。

### 机器之心近 30×24 小时

2026-07-25 02:12 UTC 使用内置资产执行：

```text
wechat_monitor.py run --target machineheart --lookback-hours 720 --backfill --dry-run
```

证据：

- `run_status=success`
- `coverage=partial`
- 官网 sitemap `status=ok`
- 当前滚动窗口发现 343 条官网文章元数据
- `candidates_rejected=0`、`source_failures=0`
- `content_valid=0`、`summaries_available=0`
- 未访问默认禁用的官方 RSS 或 xInfinite

343 是该时刻官方 sitemap 对滚动窗口的观测值，不是固定常量，也不是微信公众号完整文章数。
抽样 `https://www.jiqizhixin.com/articles/2026-07-24-7` 得到
`content_status=blocked`、`content_reason=publisher_data_service_gate`，没有绕过或据 URL slug
生成摘要。

### 结构、文档和 Pola 门禁

```text
quick_validate.py pola-wechat-public-account-reader
结果：Skill is valid!

validate_function_test_cases.py ...
结果：PASS，覆盖 12 个验收项、7 个 feature、18 个 case

validate_pola_skills.py
结果：PASS: Pola skill harness found no issues.
```

Python compile、三份资产 `validate`、本地 Markdown 链接、尾随空白和定向 secret 扫描均通过。

### 宿主验证

- 临时 HOME：Codex、Claude Code、Qoder、Qoder Work 四种用户目录的 check/apply/idempotency/
  conflict/repair 行为通过。
- Codex 实机：
  `~/.codex/skills/pola-wechat-public-account-reader` 已指向
  `PolaSkills/pola-wechat-public-account-reader`，状态 `ready`；从安装路径运行离线 harness 通过。
- Claude Code、Qoder、Qoder Work：未修改用户真实目录，也未声称完成运行时触发测试；安装后仍需在
  对应宿主执行 smoke。Qoder/Qoder Work 的符号链接发现能力尤其不能仅由临时文件系统测试证明。

## 集成回归

artifact: regression-evidence

- 旧入口：`validate`、`run`、`cron` 保持可用。
- 旧配置：`version=1` 和未使用新字段的 RSS 行为保持兼容。
- 旧状态：SQLite schema、历史记录、按 target 去重和事务路径通过。
- 失败路径：来源 schema/身份异常进入 `source_unavailable`，不伪装成零更新。
- UI/浏览器：不适用；本次仅 CLI、报告和 Skill 文件，无图形界面或页面改动，因此没有截图。
- 生产副作用：无。

## 残余风险

- 免费公网来源只能给出 `partial` coverage；sitemap 不是公众号完整历史。
- 机器之心示例的 1000 条 sitemap 上限适合当前 30 天窗口；扩大窗口前需复核最旧日期和截断状态。
- 微信正文、上游 schema、站点风控和服务权益会变化；外部退化必须告警，不能解释成没有更新。
- 机器之心免费 RSS 当前不支持 AI Agent；没有相应订阅或书面授权时不能启用。
- Qoder/Qoder Work 的实际符号链接发现仍需各宿主 smoke。
