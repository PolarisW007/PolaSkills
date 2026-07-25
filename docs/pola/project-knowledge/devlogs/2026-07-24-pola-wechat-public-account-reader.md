# 开发日志：pola-wechat-public-account-reader

artifact: finalization

## 目标

交付一个可安装的 Codex Skill，通过纯公网、免费来源优先监控微信公众号及其它媒体更新，形成可追溯摘要和来源健康报告，并提供可重复 harness。

风险等级：P2。涉及定时抓取、外部网络、SQLite、日志和 AI 摘要输入。

## 核心决策

- 将“发现文章”和“读取正文”拆成两个阶段。
- 默认使用 Album、Homepage、RSS/Atom 和已知文章 URL。
- 用可选 `json_api` adapter 承接合规远程 provider，不硬编码供应商。
- `biz` 是微信账号稳定身份；名称和 `wxid` 不作为文章归属证据。
- 免费源零结果只表达为 `observed_zero/partial`，来源全失败为 `unknown`。
- 第一次监控默认 `from_now` 建立基线；历史回补必须显式 `--backfill`。
- 使用 SQLite、文件锁、请求超时、响应大小、页数、正文预算和报告保留天数控制稳定性。
- 正文 CAPTCHA 不进入摘要；独立脚本提供摘录式 fallback，Agent 再深化。
- HTTPS 禁用环境代理，DNS 解析结果固定到实际连接；带 provider secret 的请求禁止跨 origin 重定向。
- 文章按 `(target_id, article_key)` 去重；运行、来源、候选和 SQLite 证据均有容量或保留期边界。

## 变更

### Skill

- 新增 `pola-wechat-public-account-reader/SKILL.md`。
- 新增 Codex `agents/openai.yaml`。
- 新增来源、配置和运维 reference。
- 新增包含 `instachina` 身份和公开 Album 演示的安全配置示例。

### Runtime

- 新增 `validate`、`run`、`cron` CLI。
- 新增 Album、Homepage、RSS/Atom、article URL 和 JSON API adapter。
- 新增 DNS 固定 HTTPS/SSRF 门禁、Cookie header 拒绝、鉴权跨域重定向阻断、请求和整轮运行 deadline。
- 新增 `biz` 校验、URL canonicalization 和文章唯一键。
- 新增 SQLite 文章、运行、source observation 和 checkpoint。
- 新增来源状态/coverage 聚合、JSON/Markdown 原子报告和保留期清理。
- 新增微信 CAPTCHA、环境异常、登录页和非正文质量门禁。
- 新增摘录摘要和来源摘要 fallback。
- 新增目标/来源/候选上限、数据库历史清理和旧版 article 主键无损迁移。
- 新增最大 1 MiB 普通配置文件门禁和 10 秒初始读取 guard，避免 FIFO/超大文件堆积。
- 新增 env header 字符门禁与常量化错误，避免 secret 进入报告或 SQLite。
- 新增 cron 路径控制字符及 `%` 拒绝，阻断 crontab 换行注入和命令截断。

### Harness

- 新增 5 个 fixtures。
- 新增 38 项离线测试。
- 新增显式 `--live` 公网 smoke；外部退化使用退出码 2。
- 新增需求/架构验收用例 JSON。

## 验证

- Codex quick validator：Pass。
- Python compileall：Pass。
- 离线 harness：38/38 Pass。
- 公网 smoke：Album 20、Homepage 20，全部 `biz` 匹配；正文 CAPTCHA 准确阻断。
- 端到端 dry run：发现 22 篇，coverage=`partial`，无 SQLite 写入。
- function test cases validator：13 个验收项、7 个 feature、15 个 case 全部覆盖。
- Pola 全局 harness：Pass。
- 独立代码/安全复审：No blocking findings。
- 安装路径 quick validator 和 harness：Pass。
- cron 注入样例：拒绝并返回退出码 2。

详细证据：

- `test-reports/2026-07-24-pola-wechat-public-account-reader.md`

## 稳定性与安全门禁

- 并发：默认串行。
- 运行锁：启用。
- HTTP 超时：默认 15 秒。
- 整轮 deadline：默认 600 秒，CLI 另有进程级 SIGALRM 兜底。
- 响应大小：默认 5 MiB。
- 分页：默认最多 3 页。
- 配置容量：最多 100 个目标、每目标 10 个来源、每轮 5,000 个候选。
- 正文预算：每轮最多 10 篇。
- 回看：默认 72 小时；支持 168 小时回补。
- 报告/运行证据保留：默认 30 天；文章键和 checkpoint 保留以维持去重。
- Secret：只允许远程 provider 从环境变量读取；Cookie header 和鉴权跨域重定向被拒绝。
- Secret 错误：非法 env header 在请求前以固定脱敏错误拒绝，不记录 header 值。
- SSRF：禁用环境代理；DNS 地址通过公网校验后固定到实际 TLS 连接。
- 日志：cron 默认覆盖 `last-run.log`，避免无界追加。
- 发布：未创建生产 cron、未发送通知、未配置商业 API。

## 安装

- 源码位于用户指定的 skills 仓库。
- Codex skills 目录中已创建同名符号链接。
- 安装后验证通过；Skill 将在后续 Codex turn 中可用。

## Git 与外部同步

- 当前仓库原本存在大量与本任务无关的未提交改动，本次未触碰也未纳入。
- 本次新增目录尚未 commit；用户未要求提交或 push。
- 已运行内容和 secret 关键词扫描；没有提交真实 key、Cookie、Token 或私钥。
- 钉钉开发日志与 AI 表格未同步：本任务没有授权外部写入。此项记录为项目流程残余项，不影响本地 Skill 和 Codex 安装。

建议 commit：

```text
feat: 新增公众号公网监控与摘要 Skill

- 支持 Album、Homepage、RSS 和远程 JSON API
- 增加 biz 校验、SQLite 去重、来源健康与 CAPTCHA 门禁
- 补充 Pola 需求架构、38 项 harness 和公网验证证据
```
