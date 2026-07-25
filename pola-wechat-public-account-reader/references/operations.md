# 定时运行与稳定性

## 推荐频率

- 关键目标：每 15 分钟。
- 普通目标：每 60 分钟。
- 仅有 sitemap 的官网审计目标：每日或每周；不要按 15 分钟高频抓取大型 sitemap。
- 在分钟值上分散任务，避免整点集中。
- 每次实时检查回看 72 小时。
- 每晚单独执行一次 168 小时回补。
- 每周人工抽查 30 天，比较来源并集和已知文章。

这些频率是稳定性上限建议。私人模式也应根据 429、封禁、网络、CPU、磁盘和报告增长情况降低
频率，不进行无界重试。

## 批量任务拆分

单份配置可以维护多个 target。默认 `run` 处理所有启用目标；生产中可按优先级拆成不同频率：

```bash
python3 scripts/wechat_monitor.py cron \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --schedule "7 * * * *" \
  --priority critical

python3 scripts/wechat_monitor.py cron \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --schedule "23 */3 * * *" \
  --priority normal \
  --priority low
```

过滤器也支持重复的 `--target` 和 `--exclude-target`。多个调度任务共享同一 SQLite 时受同一个
运行锁保护；锁冲突会明确退出，不并发抓取。同一个 target 不应被多个定时表达式重叠调度，否则会
产生不必要的锁冲突和重复网络访问。

生成命令只打印 crontab 行。检查绝对路径、环境变量注入、输出目录和恢复方案后，再由用户显式
安装到调度器。

## 运行锁与幂等

CLI 默认对 SQLite 路径建立非阻塞文件锁。已有任务运行时，新任务返回退出码 2。

文章唯一键优先级：

1. `biz + mid + idx + sn`
2. canonical URL
3. `biz + published_at + title`

先原子写报告，再提交 SQLite 事务。进程在两者之间崩溃可能导致下次重复报告，但不会丢失文章。

## 超时和容量

- 每个 HTTP 请求默认 15 秒。
- 单次运行默认最多 600 秒；达到 deadline 后停止继续访问来源。
- 初始配置读取另有 10 秒进程 guard，且仅接受最大 1 MiB 的普通文件。
- 单个响应最大 5 MiB。
- 每个列表源默认最多 3 页。
- 默认最多 100 个目标、每目标 10 个来源、单次 5,000 个候选。
- 每个来源默认最多接收 100 个候选，每个目标默认最多 1,000 个候选，整轮最多 5,000 个。
- 每次最多抓取 10 篇新文章正文，每个目标默认最多 3 篇。
- 正文预算按 `critical → normal → low` 分层，同优先级 target 轮询；一个大号不能耗尽同级
  后续目标的逐篇读取预算。
- 正文最多保留 200 KiB 清洗文本，报告只放摘要输入。
- sitemap gzip 解压后默认最多 10 MiB；DOCTYPE 和 ENTITY 会被拒绝。
- 报告、SQLite 运行记录和来源观察默认保留 30 天，`latest.*` 始终覆盖；文章去重键与 checkpoint 保留。

不要无界保存 HTML、图片、全文或网络调试日志。

cron 命令中的路径不得包含 CR、LF、NUL 或 `%`；CLI 会在输出前拒绝这些路径，避免 crontab 换行注入或 `%` 截断。

## 失败处理

- 网络、429、5xx：有限重试和退避。
- 401/403、验证码、环境异常：立即标记 `blocked`，不要循环重试。
- schema 变化：标记 `source_unavailable` 并告警。
- 环境变量 Token 缺失：该来源标记 `missing_secret`；不把 query 或 Token 写入错误和报告。
- 标准模式下 `permission_required=true` 且缺引用：配置校验失败，不访问公网。
- 私人模式下空引用：记录 permission override；其它技术校验继续。
- 单来源失败：继续其它来源。
- 全来源失败：coverage=`unknown`，退出码 1。

混合批次要区分三种结论：

- 全部选中目标的来源健康且没有新增：`no_new_articles_observed`。
- 至少一个目标失败或覆盖未知：`coverage_incomplete`；即使其它目标零新增，也不能写成“全部无更新”。
- 所有目标来源失败：coverage=`unknown`，只能报告监控盲区。

Markdown 和 JSON 报告都应保留 `selection`、逐目标状态、失败来源和拒绝候选数。批量中某个目标失败
不会取消其它目标已获得的观察证据。

## 机器之心运行建议

机器之心私人案例有效启用 sitemap、官方 RSS 和 xInfinite：

```bash
python3 scripts/wechat_monitor.py run \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --target machineheart \
  --lookback-hours 720 \
  --backfill \
  --private-use
```

注意：

- sitemap 是发布方网站文章流，不是微信公众号完整历史；coverage 必须保持 `partial`。
- 私人配置用 `fetch_content_in_private_mode=true` 尝试 sitemap 正文。官网文章返回数据服务提示页
  时仍识别为 `publisher_data_service_gate`，不进入摘要。
- 官方 RSS 在私人模式有效启用，但服务端 Token 仍从运行环境注入；缺失时报告
  `missing_secret`，其它来源继续。
- xInfinite 只接收唯一微信原文、作者和 `biz` 均匹配的机器之心条目。
- 微信原文遇到 CAPTCHA 时不要重试对抗风控；保留标题、链接和可靠来源摘要。
- 官网 sitemap 与官方 RSS 属于同一
  `independence_group=machineheart_official`，不能计算为两个独立上游。

## 告警建议

至少包含：

- 新文章：公众号、标题、时间、摘要、链接和验证状态。
- 监控盲区：目标、失败来源、首次/连续失败时间。
- 身份异常：候选 `__biz` 与目标不一致。
- 内容异常：验证码或正文质量不足。

摘要失败不阻止新文章元数据告警。

## 生产前观察指标

- 发现召回率。
- P50/P95 发现延迟。
- `source_unavailable` 和 `blocked` 比例。
- 错误公众号归属数。
- 重复告警率。
- 正文有效率。
- 每个验证后新文章的 API 成本。

## 不影响原功能的验证路径

本 Skill 独立运行，不修改：

- PolaNews 旧 RSS 入口和摘要。
- 旧 API 和历史数据。
- 任何微信登录态。
- 已有 Skill、cron、通知或部署配置。

安装只增加 Skill 目录链接；删除链接即可回滚。

## 宿主安装与断链修复

四个宿主共享同一规范 Skill 目录。默认检查不会写文件：

```bash
python3 scripts/install_hosts.py
```

只安装缺失的 Codex 链接：

```bash
python3 scripts/install_hosts.py --host codex --apply
```

若精确目标是断链，只能显式加入 `--repair-broken`。真实目录、普通文件或指向其它有效来源的链接
一律拒绝覆盖。Codex、Claude Code、Qoder 和 Qoder Work 的位置、返回码及 smoke test 见
[host-compatibility.md](host-compatibility.md)。
