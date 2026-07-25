# Pola 微信公众号公网阅读与监控

`pola-wechat-public-account-reader` 是一个标准库 Python Agent Skill，用纯公网来源批量发现指定
微信公众号及其它媒体的更新，读取可验证正文，并生成增量状态、来源健康、中文摘要素材和
JSON/Markdown 报告。它不依赖本机微信 App、扫码登录、Cookie 或客户端数据库。

完整 Agent 工作指令见 [`SKILL.md`](SKILL.md)。

## 核心能力

- 在一份 JSON 中维护最多 100 个公众号或媒体目标。
- 统一接入微信公众号公开 Album/Homepage、RSS/Atom、发布方 sitemap、已知文章 URL 和远程
  JSON API。
- 用 SQLite checkpoint 去重，按目标隔离失败，输出逐来源健康状态。
- 通过公众号 `biz`、唯一微信原文、来源作者和正文质量门禁校验文章。
- 支持 `enforced` 与显式 `private_opt_in` 两种来源策略。
- 为正文抓取设置全局和逐目标预算，并保持请求、响应、分页、解压和整轮运行有界。
- 同一份源码可检查或安装到 Codex、Claude Code、Qoder 和 Qoder Work 的 Skill 目录。

## 环境要求

- Python 3.10 或更高版本。
- macOS 或 Linux。
- 正常运行仅使用 Python 标准库。
- 公网 smoke 和真实监控需要网络；离线 Harness 不需要外部服务。

## 快速开始

```bash
git clone https://github.com/PolarisW007/PolaSkills.git
cd PolaSkills/pola-wechat-public-account-reader

python3 scripts/run_harness.py

cp assets/targets.batch.example.json /absolute/path/to/targets.json

python3 scripts/wechat_monitor.py validate \
  --config /absolute/path/to/targets.json

python3 scripts/wechat_monitor.py run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --dry-run
```

确认报告中的 `coverage`、逐来源状态和身份后，去掉 `--dry-run` 正式运行并写入 checkpoint。运行
配置、SQLite 和输出目录应放在独立工作目录，不要放进 Skill 源码目录。

## 私人 RSS/第三方来源模式

复制私人批量资产，或为任意命令显式增加 `--private-use`：

```bash
cp assets/targets.batch.private.example.json \
  /absolute/path/to/targets.private.json

python3 scripts/wechat_monitor.py run \
  --config /absolute/path/to/targets.private.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --private-use
```

`private_opt_in` 只启用配置中 `enable_in_private_mode=true` 的来源，不会重新启用停用 target 或普通
停用 source。它放开缺少 `permission_reference` 的来源门禁，但仍保留公网 HTTPS/SSRF、敏感参数、
Token、CAPTCHA、登录页、作者/`biz` 和正文质量校验。

## 机器之心近 30 天示例

内置私人资产同时尝试官网 sitemap、官方 RSS 和 xInfinite：

```bash
python3 scripts/wechat_monitor.py validate \
  --config assets/targets.machineheart.private.json

python3 scripts/wechat_monitor.py run \
  --config assets/targets.machineheart.private.json \
  --state /absolute/path/to/machineheart.sqlite \
  --output /absolute/path/to/machineheart-output \
  --target machineheart \
  --lookback-hours 720 \
  --backfill \
  --dry-run
```

官方 RSS 仍需要运行环境中的 `MACHINEHEART_RSS_TOKEN`；未设置时仅该来源返回 `missing_secret`，
sitemap 和 xInfinite 继续运行。所有来源的 coverage 仍为 `partial`，不能据此宣称公众号文章完整
覆盖。详见
[`references/cases/machineheart-monitoring.md`](references/cases/machineheart-monitoring.md)。

## 批量目标与筛选

默认运行配置中全部启用目标。可重复使用：

```bash
python3 scripts/wechat_monitor.py run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --priority critical \
  --priority normal \
  --exclude-target temporarily-paused
```

也可重复传入 `--target <id>` 只运行指定目标。过滤器只能缩小启用集合，不能重新启用
`enabled=false` 的目标。

## 定时监控

生成 cron 示例：

```bash
python3 scripts/wechat_monitor.py cron \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --schedule "17 * * * *"
```

该命令只打印 crontab 行，不会自动安装。正式调度前应审查路径、频率、运行锁、容量预算、报告保留
和告警出口。详细说明见 [`references/operations.md`](references/operations.md)。

## 安装为 Agent Skill

默认只读检查全部支持宿主：

```bash
python3 scripts/install_hosts.py
```

显式安装 Codex：

```bash
python3 scripts/install_hosts.py --host codex --apply
```

安装器不会覆盖真实目录、普通文件或指向其它有效来源的链接。四宿主位置和退出码见
[`references/host-compatibility.md`](references/host-compatibility.md)。

## 验证

```bash
# 81 项离线回归
python3 scripts/run_harness.py

# 标准公网来源 smoke
python3 scripts/run_harness.py --live

# 机器之心私人 RSS/第三方来源 smoke
python3 scripts/run_harness.py --live-private
```

公网来源临时退化时 Harness 可能以退出码 2 结束；这表示离线测试通过但公网结构或访问状态需要
复核，不等同于“没有更新”。

## 更多文档

- [能力、使用方式与案例](references/capabilities-and-examples.md)
- [完整配置字段](references/configuration.md)
- [来源策略](references/source-strategy.md)
- [运行、稳定性与定时化](references/operations.md)
- [四宿主兼容](references/host-compatibility.md)
- [机器之心案例](references/cases/machineheart-monitoring.md)
