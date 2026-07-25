# 配置与命令参考

## 顶层结构

```json
{
  "version": 1,
  "defaults": {
    "lookback_hours": 72,
    "timeout_seconds": 15,
    "max_run_seconds": 600,
    "max_response_bytes": 5242880,
    "max_pages": 3,
    "max_articles_per_source": 100,
    "max_candidates_per_target": 1000,
    "max_total_candidates": 5000,
    "fetch_content": true,
    "max_content_fetches": 10,
    "max_content_fetches_per_target": 3,
    "article_text_limit": 204800,
    "max_sitemap_uncompressed_bytes": 10485760,
    "summary_input_chars": 1800,
    "retention_days": 30,
    "baseline_mode": "from_now",
    "source_permission_policy": "enforced"
  },
  "targets": []
}
```

所有 URL 默认必须使用 HTTPS。

配置文件必须是普通文件，最大 1 MiB；FIFO、设备文件和无界配置会在联网前被拒绝。

`baseline_mode=from_now` 在首次成功运行时只建立现状基线，避免把全部历史当作新告警；查询近一周、近一月或明确回补时使用 `--backfill`，或者把
`baseline_mode` 配置为 `backfill_window`。

`source_permission_policy` 支持：

- `enforced`：默认值；保持 V2 的来源许可引用门禁。
- `private_opt_in`：私人用途；放宽空 `permission_reference`，并启用明确标记的私人来源。

CLI 的 `--private-use` 会把本轮策略覆盖为 `private_opt_in`。validate 和运行报告都会显示最终策略，
因此定时任务不会静默切换来源集合。

批量护栏分三层：

- 每个来源默认最多接收 `max_articles_per_source=100` 篇。
- 每个目标默认最多归一化 `max_candidates_per_target=1000` 篇。
- 整轮默认最多归一化 `max_total_candidates=5000` 篇。

正文预算也有两层：整轮最多 `max_content_fetches` 篇，每个目标最多
`max_content_fetches_per_target` 篇。分配先按 `critical → normal → low`，同优先级目标轮询。
没有获得正文预算的文章不会丢失；它保留元数据和可信的来源摘要，并标记
`content_status=not_fetched`。

## Target

```json
{
  "id": "instachina",
  "name": "InsDaily",
  "wxid": "instachina",
  "biz": "MzI2MTcxMjI0MQ==",
  "priority": "normal",
  "enabled": true,
  "sources": []
}
```

- `id`：本地稳定 ID，只允许字母、数字、点、下划线和连字符。
- `biz`：微信文章和列表接口的稳定账号标识。
- `wxid`：用于人工识别和检索，不代替 `biz` 做归属校验。
- `priority`：`critical`、`normal` 或 `low`。
- `enabled=false`：保留目标但跳过运行；允许暂时没有来源。
- 每个启用目标至少配置一个启用来源。

`enabled` 和 `fetch_content` 等布尔字段必须是 JSON 布尔值 `true`/`false`；字符串
`"true"`、`"false"` 会被拒绝。最多配置 100 个目标，每个目标最多 10 个来源。启用目标的
`id` 和非空 `biz` 必须唯一。

## 来源通用字段

所有来源可使用：

```json
{
  "id": "publisher-primary",
  "type": "rss",
  "enabled": true,
  "independence_group": "publisher_site",
  "completeness": "partial",
  "max_pages": 2,
  "max_articles": 100,
  "fetch_content": true,
  "enable_in_private_mode": false,
  "fetch_content_in_private_mode": false,
  "permission_required": false,
  "permission_reference": ""
}
```

- `id` 可省略；省略时系统按来源类型和不含 secret 的公开定位符生成稳定 key。同一 target
  内不允许重复 source key。
- `max_pages` 只影响分页来源；`max_articles` 覆盖该来源的候选上限。
- `fetch_content=false` 只禁止该来源触发逐篇正文请求，不影响发现和来源摘要。
- `enable_in_private_mode=true` 只在 `private_opt_in` 下把该来源有效启用；普通停用来源和停用
  target 不受影响。
- `fetch_content_in_private_mode=true` 只在私人模式下把正文尝试打开，仍受正文预算与质量门禁。
- 免费公网来源默认 `completeness=partial`。只有经过审计的 `json_api` 且显式设置
  `complete_source_acknowledged=true` 才能声明 `complete`。
- `permission_required=true` 保留来源策略元数据。在 `enforced` 下启用来源必须填写不含密钥的
  `permission_reference`；在 `private_opt_in` 下空引用会变成 warning 和
  `permission_overrides` 证据，不阻止运行。

## 微信 Album

```json
{
  "type": "wechat_album",
  "album_id": "4482506796406177793",
  "is_reverse": "0",
  "independence_group": "wechat_public",
  "enabled": true
}
```

必须在 target 上设置 `biz`。

`is_reverse=0` 请求最新文章优先；只有在人工确认要从最旧文章开始回补时才设为 `1`。

## 微信 Homepage

```json
{
  "type": "wechat_homepage",
  "hid": "16",
  "cid": "0",
  "independence_group": "wechat_public",
  "enabled": true
}
```

`cid` 可省略。Homepage 只适用于具有公开主页模板的公众号。

## RSS/Atom

```json
{
  "type": "rss",
  "url": "https://publisher.example/feed.xml",
  "entry_link_mode": "feed",
  "independence_group": "publisher_site",
  "enabled": true
}
```

RSS 可以指向公众号之外的独立媒体。若 feed 由同一个微信 endpoint 包装生成，不要伪装成独立来源组。

需要把 Token 放入 URL query 时，只声明环境变量映射：

```json
{
  "type": "rss",
  "url": "https://publisher.example/rss",
  "query_from_env": {
    "token": "PUBLISHER_RSS_TOKEN"
  },
  "entry_link_mode": "feed",
  "enabled": true
}
```

请求 URL 只在内存中构造；配置、source key、错误、报告和 SQLite 都不保存环境变量值。
环境变量缺失时该来源返回 `missing_secret`，其它目标继续运行。带 secret query 的请求禁止跨
origin 重定向，错误只显示 origin 和 path。

feed 的 description 中包含微信原文时，可使用：

```json
{
  "type": "rss",
  "url": "https://authorized.example/account.rss",
  "entry_link_mode": "wechat_original_from_description",
  "expected_author": "目标公众号名称",
  "permission_required": true,
  "permission_reference": "contract-or-written-approval-id",
  "timestamp_shift_hours": 0,
  "max_future_hours": 1,
  "independence_group": "authorized-provider",
  "enabled": true
}
```

此模式要求 target 配置 `biz`。每个 feed item 必须只有一个可验证的
`mp.weixin.qq.com` 原文链接；链接 `biz` 或 `expected_author` 不匹配时拒绝该 item。
feed item 链接保存为 `source_url`，微信链接保存为主 `url` 和 `original_url`。不配置
`entry_link_mode` 时保持普通 feed 行为。

`timestamp_shift_hours` 只用于发布方 feed 存在已确认固定时区偏差的情况，范围为 -24 至 24；
不要用它猜测发布时间。`max_future_hours` 控制允许的发布时钟超前量，范围为 0 至 24，缺省为
24；对质量不稳定的第三方 feed 建议设置为 1，避免把明显的未来时间戳误计为当前增量。
若当前窗口里的条目全部超过该容差，来源状态为 `source_unavailable/source_entries_rejected`，不会
误报成“零更新”。

## 发布方 sitemap

```json
{
  "type": "sitemap",
  "url": "https://publisher.example/sitemap.xml.gz",
  "url_pattern": "^https://publisher\\.example/articles/",
  "published_at_from_url": "/articles/(\\d{4}-\\d{2}-\\d{2})",
  "nested_https_url": false,
  "fetch_content": false,
  "independence_group": "publisher_site",
  "enabled": true
}
```

- `url_pattern` 必填，只接收匹配该正则的文章 URL。
- `published_at_from_url` 可省略；填写时必须只有一个捕获组。系统不会把 sitemap
  `lastmod` 默认当成发布时间。
- 某些发布方 sitemap 的 `<loc>` 错误地把真实 HTTPS URL 嵌在另一 URL 后面；只有人工确认后
  才设置 `nested_https_url=true`。
- `.gz` 解压受 `max_sitemap_uncompressed_bytes` 限制，DOCTYPE/ENTITY 被拒绝。
- sitemap 默认 `fetch_content=false`，适合低成本缺口审计。它描述媒体网站 URL，不等于公众号
  完整历史。

## 已知文章 URL

```json
{
  "type": "article_url",
  "url": "https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=1&sn=...",
  "enabled": true
}
```

只用于验证和读取这一篇文章。

## 通用 JSON API

```json
{
  "type": "json_api",
  "url": "https://provider.example/api/articles?biz=Mz...",
  "method": "GET",
  "headers_from_env": {
    "Authorization": {
      "env": "WECHAT_PROVIDER_API_KEY",
      "prefix": "Bearer "
    }
  },
  "list_path": "data.items",
  "field_map": {
    "title": "title",
    "url": "url",
    "published_at": "publish_time",
    "summary": "digest",
    "biz": "biz"
  },
  "independence_group": "provider_example",
  "enabled": true
}
```

- Secret 只通过 `headers_from_env` 读取。
- Header 名、prefix 和环境变量值必须是有界可打印 ASCII；非法值只返回脱敏错误码。
- 带这些鉴权 header 的请求不允许跨 origin 重定向；provider 应返回同 origin 结果或无鉴权的公开下载 URL。
- `list_path` 和字段路径使用点号访问嵌套对象。
- `method` 只允许 `GET` 或 `POST`。
- 没有经过 POC 和合同验证时，coverage 仍为 `partial`。

配置最多包含 100 个目标，每个目标最多 10 个来源。`max_total_candidates` 是单次运行所有来源归一化候选的硬上限。

## 机器之心配置

机器之心的公开身份为：

```json
{
  "id": "machineheart",
  "name": "机器之心",
  "wxid": "almosthuman2014",
  "biz": "MzA3MzI4MjgzMw==",
  "priority": "critical",
  "enabled": true
}
```

标准资产 `targets.machineheart.json` 只有效启用官网 sitemap。私人资产
`targets.machineheart.private.json` 设置 `source_permission_policy=private_opt_in`，因此有效启用
官网 sitemap、官方 RSS 和 xInfinite：

```json
{
  "id": "machineheart",
  "name": "机器之心",
  "wxid": "almosthuman2014",
  "biz": "MzA3MzI4MjgzMw==",
  "priority": "critical",
  "enabled": true,
  "sources": [
    {
      "id": "machineheart-official-rss",
      "type": "rss",
      "url": "https://mcp.applications.jiqizhixin.com/rss",
      "query_from_env": {
        "token": "MACHINEHEART_RSS_TOKEN"
      },
      "independence_group": "machineheart_official",
      "fetch_content": true,
      "permission_required": true,
      "enable_in_private_mode": true,
      "enabled": false
    },
    {
      "id": "machineheart-official-sitemap",
      "type": "sitemap",
      "url": "https://www.jiqizhixin.com/shared/sitemap.xml.gz",
      "nested_https_url": true,
      "url_pattern": "^https://(?:www\\.)?jiqizhixin\\.com/articles/\\d{4}-\\d{2}-\\d{2}-[A-Za-z0-9._~-]+/?(?:[?#].*)?$",
      "published_at_from_url": "/articles/(\\d{4}-\\d{2}-\\d{2})-",
      "independence_group": "machineheart_official",
      "fetch_content": false,
      "fetch_content_in_private_mode": true,
      "enabled": true
    },
    {
      "id": "xinfinite-permission-gated",
      "type": "rss",
      "url": "https://www.xinfinite.net/latest.rss",
      "entry_link_mode": "wechat_original_from_description",
      "expected_author": "机器之心",
      "max_future_hours": 1,
      "permission_required": true,
      "enable_in_private_mode": true,
      "enabled": false
    }
  ]
}
```

私人模式会把两个 `enable_in_private_mode=true` 的 RSS 有效启用，并把 sitemap 的正文尝试打开。
官方 RSS 的服务端仍要求 Token；只在任务环境中设置：

```bash
export MACHINEHEART_RSS_TOKEN="replace-with-runtime-secret"
```

不要把真实值写入 JSON、文档、日志或 Git。没有 Token 时官方 RSS 返回 `missing_secret`，sitemap
和 xInfinite 继续运行。RSS 与 sitemap 的
`independence_group` 相同，因为它们都由机器之心发布方控制；二者只能提高可观测性，不能证明
公众号近 30 天完整覆盖。xInfinite 使用独立 `xinfinite_mirror` 组，但仍保持 `partial`。

官网文章可能返回 HTTP 200 的数据服务提示页，而不是正文；正文质量门禁继续将其拒绝为
`publisher_data_service_gate`。私人模式不把提示页、登录页或 CAPTCHA 当正文。

xInfinite 的 description 必须恰好包含一个可验证微信原文，并匹配作者“机器之心”和目标 `biz`；
其它 feed item 会被拒绝。该来源额外使用一小时未来时间容差，跳过明显超前的发布时间；不会用
固定时区偏移猜测真实发布时间。所有条目被拒时来源必须是 `source_unavailable`，不能误报为零更新。

## 命令

验证：

```bash
python3 scripts/wechat_monitor.py validate --config /abs/targets.json
```

用同一份标准配置做私人模式覆盖：

```bash
python3 scripts/wechat_monitor.py validate \
  --config /abs/targets.json \
  --private-use
```

运行：

```bash
python3 scripts/wechat_monitor.py run \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --lookback-hours 72 \
  --private-use
```

使用 `--dry-run` 时不写 SQLite，也不更新 checkpoint。

默认运行所有 `enabled=true` 的目标。批量筛选参数可重复：

```bash
python3 scripts/wechat_monitor.py run \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --target machineheart \
  --target another-account

python3 scripts/wechat_monitor.py run \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --priority critical \
  --priority normal \
  --exclude-target temporarily-paused
```

- 多个 `--target` 是并集，多个 `--priority` 是并集。
- ID 和 priority 条件同时出现时取交集，再应用 `--exclude-target`。
- 选择顺序保持配置文件顺序。
- 未知 target、显式选择 disabled target 或过滤后为空会在联网前以配置错误退出。
- 报告的 `selection` 保存本轮过滤器、选中 target ID、选中数和配置总数。

输出 cron：

```bash
python3 scripts/wechat_monitor.py cron \
  --config /abs/targets.json \
  --state /abs/state.sqlite \
  --output /abs/output \
  --schedule "17 * * * *"
```

`cron` 同样接受可重复的 `--target`、`--exclude-target`、`--priority` 和 `--private-use`，并把
它们原样写入生成的运行命令。该命令只打印 crontab 行，不会安装。

## 退出码

- `0`：命令完成；可能存在部分来源失败，应继续检查报告。
- `1`：运行失败或所有目标 coverage 为 `unknown`。
- `2`：配置错误、重复运行锁或 live smoke 外部退化。
