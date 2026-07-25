# 开发日志：pola-wechat-public-account-reader V3 私人宽松模式

artifact: finalization

## 目标和风险

为个人情报监控增加显式私人来源策略，批量打开标记过的 RSS/第三方来源，并升级机器之心三来源
案例、报告审计、测试和 Codex 安装态。

风险等级：**P2**。涉及公网抓取、RSS、定时任务、secret、第三方时间戳和正文批处理。

## 核心决策

- 默认继续使用 `enforced`；只有配置或 CLI 明确选择时使用 `private_opt_in`。
- 私人模式只豁免缺少 `permission_reference`，不自动启用停用 target 或普通停用 source。
- 保留公网 HTTPS/SSRF/DNS pin、secret/Cookie、跨域鉴权重定向、容量、deadline、锁、身份和正文
  真实性门禁。
- 机器之心有效启用官网 sitemap、官方 RSS 和 xInfinite；三者仍标记 `partial`。
- 官方 RSS 缺 Token 时局部 `missing_secret`；不生成、猜测或绕过 Token。
- 第三方未来时间戳用可配置容差处理，不猜固定时区；全是异常条目时报告来源失败。

## 实现变更

- `config.py`：增加 `enforced/private_opt_in`、私人来源/正文有效状态、override warning、
  `max_future_hours` 和敏感 query 归一化。
- `runner.py`：CLI 覆盖后的来源选择审计、候选 URL 敏感参数复用和报告 selection。
- `sources.py`：未来时间容差；超限计拒绝并防止假零更新。
- `reporting.py`：JSON 保留三个审计列表，Markdown 显示数量和 target/source 标识。
- `wechat_monitor.py`：`validate/run/cron --private-use`，cron 只打印且参数不重复。
- `run_harness.py`：增加 `--live-private`，隔离官方 RSS 缺 Token并验证 xInfinite 微信身份。
- `assets/`：增加机器之心和批量私人资产；标准资产默认行为不变。
- `agents/openai.yaml`：默认提示保持 `enforced`，仅在用户明确请求时选择私人模式。
- `tests/test_private_mode.py`：策略、资产、CLI、报告、Token 隔离、URL、未来时间和工程文档回归。
- 根 `README.md`、Skill `README.md`、`SKILL.md`、`references/` 和
  `docs/pola/project-knowledge/`：补齐 GitHub 快速使用说明、能力、配置、案例、Requirement、
  PRD、SDD、测试矩阵、测试报告和本日志。

## 验证

- 离线 harness：81/81 Pass，`OFFLINE_HARNESS_OK`。
- Codex 安装路径离线 harness：81/81 Pass。
- 标准公网 harness：Album 20、Homepage 20、sitemap 20，`live_status=pass`。
- 私人公网 harness：Pass；xInfinite 11 条，官方 RSS `missing_secret`，微信正文抽样 CAPTCHA。
- 机器之心 720 小时：sitemap 343 + xInfinite 11 = 354 条动态候选；11 条来源摘要；可信全文 0；
  官方 RSS 缺 Token，因此整体 `degraded/partial`。
- 未来时间护栏：最新接受时间早于发现时间，超容差条目未进入结果。
- Skill quick validator、12 项验收/6 feature/14 case 矩阵 validator 和 Pola 全局 harness：Pass。
- 独立安全终审：Pass，无 blocker。
- 详细证据：`test-reports/2026-07-25-pola-wechat-public-account-reader-v3-private-mode.md`。

## 稳定性与“不影响功能使用”门禁

- 旧入口 `validate/run/cron`、旧 `version=1` 配置、旧 SQLite、历史去重和全部来源 adapter 回归。
- 默认串行；运行锁、请求/运行超时、响应/解压/分页/候选/正文上限和失败隔离保持。
- 报告和日志不包含真实 Token、Cookie 或环境变量值。
- 无 UI 页面；无需截图。未安装生产 cron、重启服务、发送通知、清理日志或轮换密钥。
- 回滚：移除 `--private-use` 或把策略恢复为 `enforced`，无需迁移状态库。

## 安装和同步

- 规范源码：`$REPO_ROOT/pola-wechat-public-account-reader`。
- Codex：`$CODEX_HOME/skills/pola-wechat-public-account-reader` 指向规范源码。
- 四宿主安装器在临时 HOME 回归；未修改 Claude Code、Qoder 或 Qoder Work 的真实用户目录。

## Git 与外部同步

- 源工作树存在用户既有无关改动和暂存文件；GitHub 发布使用目标空仓库的干净临时 clone，只纳入
  根使用说明、Skill 子目录和 `docs/pola/` 工程记录。
- 发布目标：`PolarisW007/PolaSkills`。该仓库首次发布前为空，因此使用独立初始提交，不携带源
  工作树的无关历史。
- commit ID 和 GitHub URL 在最终交付回执中记录，避免在同一提交中写入自引用 hash。
- 未同步钉钉或其它外部系统；当前任务没有相应外部写入授权，作为收尾残余项记录。
