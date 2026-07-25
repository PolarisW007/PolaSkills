# 测试报告：pola-wechat-public-account-reader

artifact: test-evidence

## 测试结论

**Pass。** Skill 的离线逻辑、真实公网列表发现、正文阻断识别、安装路径和 Pola 文档门禁均通过。

没有创建生产定时任务、发送外部通知或消费商业 API。

## 测试矩阵

| 验收 | 风险 | 测试 | 结果 |
| --- | --- | --- | --- |
| A1–A2 | Skill 结构 | quick_validate、Pola harness | Pass |
| A3–A4 | 公网来源/身份 | Album、Homepage、RSS/Atom、JSON fixture；live smoke | Pass |
| A5–A6 | 状态/幂等 | 部分失败、全失败、两次 SQLite 运行、回滚、锁 | Pass |
| A7–A8 | 正文/摘要 | 正常 HTML、HTTP 200 长 CAPTCHA、来源摘要 fallback | Pass |
| A9–A10 | Harness/联网 | 38 项离线测试、两个微信公网列表端点 | Pass |
| A11–A12 | 定时/安装 | cron 生成与注入拒绝；安装路径二次验证 | Pass |
| A13 | 安全/容量 | DNS 固定连接、鉴权重定向、总 deadline、跨目标去重、DB 留存和迁移 | Pass |

完整用例矩阵：

- `delivery/pola-wechat-public-account-reader/function_test_cases.json`

## 已运行命令与证据

### Codex Skill 结构

```text
python3 .../skill-creator/scripts/quick_validate.py pola-wechat-public-account-reader
结果：Skill is valid!
```

### Python 与离线 Harness

```text
python3 -m compileall -q pola-wechat-public-account-reader
结果：退出码 0

python3 pola-wechat-public-account-reader/scripts/run_harness.py
结果：38 tests，全部通过，OFFLINE_HARNESS_OK
```

覆盖内容：

- 配置和 Cookie/私网 URL 拒绝。
- Album object/array/null 和 cursor。
- Homepage、RSS、Atom、JSON API。
- `biz` 错配和 canonical URL 去重。
- 200 长 CAPTCHA 与正常正文。
- `observed_zero`、`partial`、`unknown` 和来源失败。
- 首次 `from_now` 基线、回补和第二次运行去重。
- SQLite 回滚、checkpoint 和运行锁。
- 全文摘录摘要、来源摘要 fallback，验证码文本不进入摘要。
- provider secret 跨 origin 重定向被阻断。
- env header 控制字符在传输前被拒绝，错误信息不包含 secret。
- DNS 仅解析一次并固定到已校验公网地址；私网解析被拒绝。
- slow-trickle 响应达到总 deadline 后终止。
- 配置必须是最大 1 MiB 的普通文件；首次读取受 10 秒进程 guard 保护且不会二次读取。
- 同一 URL 在不同目标下独立去重。
- URL 中合法的 `captcha` 单词不会误伤干净来源摘要。
- 候选总量上限、SQLite 运行证据保留期和旧主键迁移。
- cron 路径 CR/LF/NUL 注入和 `%` 截断被拒绝。

### 真实公网 Smoke

```text
python3 pola-wechat-public-account-reader/scripts/run_harness.py --live
结果：退出码 0，live_status=pass
```

实测：

- 微信 Album：20 条，`status=ok`，全部 `biz` 匹配。
- 微信 Homepage：20 条，`status=ok`，全部 `biz` 匹配。
- 第一篇微信正文：`status=blocked`、`reason=captcha`。
- 结论：公网列表发现可用；当前出口的微信正文受到验证码限制，但没有被误收为正文。

### 真实端到端 Dry Run

使用公开 Album 示例，回看一年并显式 backfill：

```text
wechat_monitor.py run ... --lookback-hours 8760 --backfill --dry-run
```

结果：

- `run_status=success`
- `coverage=partial`
- 发现 22 篇候选
- 正文预算内前 10 篇全部准确标记 `blocked`
- 被阻断正文没有生成或保留 CAPTCHA 摘要
- 未写 SQLite 状态

### 测试用例文档 Harness

```text
validate_function_test_cases.py --prd ... --sdd ... --spec ... --cases ...
结果：PASS，覆盖 13 个验收项、7 个 feature、15 个 case
```

### Pola 全局 Harness

```text
validate_pola_skills.py
结果：PASS: Pola skill harness found no issues.
```

### 独立代码与安全复审

两层只读复审逐项复核跨域 secret、DNS pinning、deadline、跨目标去重、摘要污染、SQLite retention、配置读取和 cron 渲染，最终结论：`No blocking findings`。

### 安装后回归

从 Codex 安装路径、工作目录 `/tmp` 运行：

- quick_validate：Pass。
- 离线 harness：38 项 Pass。
- 证明脚本资源路径不依赖源码仓库当前目录。

## 集成回归结论

artifact: regression-evidence

- 环境：本机公网 + 临时目录。
- 主成功路径：配置校验 → Album/Homepage 发现 → `biz` 校验 → 报告。
- 降级路径：正文 CAPTCHA → 元数据保留 → 正文不进入摘要。
- 幂等路径：同一 SQLite 第二次运行不重复报告。
- 安全路径：鉴权跨域重定向、DNS 重绑定、慢响应和 cron 换行注入均被回归用例阻断。
- 升级路径：旧全局 `article_key` 状态库无损迁移为 `(target_id, article_key)`。
- UI/浏览器：不适用；本次没有图形界面或页面改动，因此没有截图。
- 生产副作用：无。

## 残余风险

- `instachina` 仍缺少可验证的 Album ID、Homepage HID 或官网 RSS，因此不能靠免费源证明近一周文章清单完整。
- 免费 Album/Homepage 只能覆盖合集或栏目，默认 coverage 为 `partial`。
- 微信正文当前可能被 CAPTCHA 阻断；此时只能使用来源摘要，不能生成全文语义摘要。
- 商业 provider adapter 已提供，但具体供应商、SLA、费用和合规性没有测试。
