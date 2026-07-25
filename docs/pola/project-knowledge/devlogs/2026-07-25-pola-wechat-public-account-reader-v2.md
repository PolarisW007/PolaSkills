# 开发日志：pola-wechat-public-account-reader V2

artifact: finalization

## 目标和风险

把现有纯公网公众号监控 Skill 升级为可批量维护目标列表、支持机器之心案例、兼容 Codex/Claude
Code/Qoder/Qoder Work 目录并提供完整能力/用法/案例说明的可验证交付。

风险等级：**P2**。涉及批量公网抓取、定时任务、SQLite、RSS/sitemap、凭据和报告。

## 核心决策

- 保留单一 runtime 和 `version=1` 配置，扩展现有 `targets[]`，不建立机器之心专用爬虫。
- 默认串行发现；正文预算按 `critical → normal → low`，同优先级 target 轮询。
- 机器之心免费默认只启用 robots 公示的官网 sitemap，`fetch_content=false`、coverage=`partial`。
- 免费官方 RSS 不用于 Agent；有相应权益或书面许可时才通过环境变量注入 Token。
- xInfinite 条款禁止自动监控，示例保持 `permission_required=true`、`enabled=false`。
- 四个宿主共享 `PolaSkills` 中一份规范源码；安装器默认只读，显式 apply，绝不覆盖真实目录或
  有效异源链接。
- 不自动部署 cron、不发消息、不购买服务、不修改 Claude/Qoder/Qoder Work 的真实用户目录。

## 实现变更

### 批量监控

- `config.py`：严格布尔、重复 source key、最多 100 target/每 target 10 source、逐目标候选与正文
  预算、permission gate、sitemap/RSS 新字段。
- `wechat_monitor.py`：可重复 `--target`、`--exclude-target`、`--priority`，同时支持 `run` 和
  `cron`。
- `runner.py`：选择元数据、目标失败隔离、逐条候选拒绝、critical 优先与同级 round-robin。
- `reporting.py`：schema 1.1、逐目标批量计数、`coverage_incomplete`、拒绝/截断统计和 Markdown
  安全转义。

### 来源与机器之心

- `parsers.py` / `sources.py`：授权 RSS 嵌入微信原文、作者和 `biz` 校验；gzip sitemap 有界解析；
  env query；来源级 `fetch_content`。
- 新增 `assets/targets.machineheart.json` 和 `assets/targets.batch.example.json`。
- 新增机器之心案例；官网提示页归类为
  `content_status=blocked/content_reason=publisher_data_service_gate`。
- 2026-07-25 最终公网 dry run 在滚动 720 小时内观察到 343 条官网元数据，正文和摘要均为 0，
  coverage 保持 `partial`。

### 失败真实性与安全

- RSS/Atom、sitemap 和 JSON API 校验根结构或 `list_path`；维护页、字段改名、空 URL、拒绝条目
  不得成为 `observed_zero`。
- 候选在 canonicalization 前检查 credentials 和敏感 query；非法 hostname/私网地址被拒，公网
  IPv6 保留方括号。
- 只有 `mp.weixin.qq.com` 能提供微信身份和 `wx:` 去重键，阻断跨域伪造 `__biz/mid/idx/sn`。
- query secret 只在内存构造；HTTP 异常移除 query/fragment，并切断可能携带 Token 的异常链。
- 报告对不可信名称、标题、摘要和文章 URL 做 Markdown/百分号编码。
- sitemap 解压、响应、候选、正文、deadline、运行锁和保留期均有上限。

### 四宿主和文档

- 新增 `scripts/install_hosts.py`，支持：
  - Codex：`$CODEX_HOME/skills` 或 `~/.codex/skills`
  - Claude Code：`~/.claude/skills`
  - Qoder：`~/.qoder/skills`
  - Qoder Work：`~/.qoderwork/skills`
- 新增宿主兼容、能力案例和机器之心 reference；更新配置、运维和来源策略。
- 更新 `SKILL.md` 和 `agents/openai.yaml`，明确批量、30 天回补、状态字段、授权和残余覆盖边界。

## 验证

- Python compile：Pass。
- 离线 harness：70/70 Pass。
- 公网 harness：Album 20、Homepage 20、机器之心 sitemap 20；`live_status=pass`。
- 机器之心 720 小时 dry run：343 条官网元数据，`partial`，无正文/摘要。
- Machineheart 正文抽样：`publisher_data_service_gate`。
- Skill quick validator：Pass。
- 功能用例 validator：12 个验收项、7 个 feature、18 个 case。
- Pola 全局 harness：Pass。
- 三份资产离线 validate：Pass。
- Codex 安装状态：`ready`；安装路径离线 harness：Pass。
- 独立实现/安全终审：No blocking findings。

详细证据：

- `test-reports/2026-07-25-pola-wechat-public-account-reader-v2.md`

## 稳定性与“不影响功能使用”门禁

- 旧 Album、Homepage、普通 RSS、article URL、JSON API、SQLite 和 cron 回归通过。
- `version=1` 无需迁移；新字段均有默认值。
- 默认串行；每轮和逐目标候选/正文预算、请求和运行 deadline、锁及 checkpoint 生效。
- 报告和运行证据有保留期；cron 示例覆盖 `last-run.log`，不无界追加。
- 配置、报告、SQLite 和文档均不写入真实 Token/Cookie。
- 无 UI 变更；无需浏览器截图。
- 未创建生产任务、重启服务、清理生产日志、轮换密钥或发送外部消息。

## 安装和同步

- 规范源码：`$REPO_ROOT/pola-wechat-public-account-reader`。
- Codex：`$CODEX_HOME/skills/pola-wechat-public-account-reader` 为指向规范源码的符号链接。
- 原 Codex 链接曾指向已不存在的旧工作目录；本次使用显式 `--apply --repair-broken` 修复。
- Claude Code、Qoder、Qoder Work 仅在临时 HOME 验证安装行为，未写入真实用户目录。

## Git 与外部同步

- 仓库原本存在与本任务无关的改动，本次未触碰或纳入。
- Skill 目录和项目知识目录当前仍是未跟踪交付；用户未要求 commit/push，因此未提交。
- 定向 whitespace、JSON、secret 和 `git diff --check` 门禁通过；全仓非定向检查仍可能看到用户
  既有文件问题，不归入本次交付。
- 未同步钉钉或其它外部系统；本次没有相应写入授权。
