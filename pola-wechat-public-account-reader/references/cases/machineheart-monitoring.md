# 案例：机器之心私人三来源情报监控

本案例在纯公网、无本机微信 App/Cookie/扫码登录的前提下，同时尝试机器之心官网 sitemap、官方
RSS 和 xInfinite RSS。

## 身份与来源

- 名称：机器之心
- 微信号：`almosthuman2014`
- `biz`：`MzA3MzI4MjgzMw==`
- 官网 sitemap：`https://www.jiqizhixin.com/shared/sitemap.xml.gz`
- 官方 RSS：`https://mcp.applications.jiqizhixin.com/rss`
- xInfinite：`https://www.xinfinite.net/latest.rss`

`assets/targets.machineheart.private.json` 使用 `private_opt_in`：

- sitemap 有效启用，并在每轮/逐目标正文预算内尝试正文。
- 官方 RSS 通过私人标记有效启用；服务端仍需要 `MACHINEHEART_RSS_TOKEN`。
- xInfinite 通过私人标记有效启用；只接受唯一微信原文、作者“机器之心”和目标 `biz` 均匹配的条目。
- xInfinite 使用一小时未来时间容差；明显超前的 feed 时间戳不会被计入本轮候选。

三条来源全部开启后仍是 `partial`。官网 sitemap 与官方 RSS 同属
`machineheart_official`，xInfinite 属于 `xinfinite_mirror`；来源数量不等于公众号完整历史。

## 一、复制并验证私人配置

```bash
WECHAT_READER_SKILL="/path/to/pola-wechat-public-account-reader"

cp "$WECHAT_READER_SKILL/assets/targets.machineheart.private.json" \
  /absolute/path/to/targets.machineheart.private.json

python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" validate \
  --config /absolute/path/to/targets.machineheart.private.json
```

成功结果应包含：

- `source_permission_policy=private_opt_in`
- `enabled_sources=3`
- 两个 `private_mode_enabled_sources`
- 一个 `private_mode_content_sources`
- 两个 `permission_overrides`

同样可以对标准资产传入 `--private-use`，得到相同的有效来源集合：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" validate \
  --config /absolute/path/to/targets.machineheart.json \
  --private-use
```

## 二、运行三来源 dry run

没有官方 RSS Token 也可以先运行：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.machineheart.private.json \
  --state /absolute/path/to/machineheart.sqlite \
  --output /absolute/path/to/machineheart-output \
  --target machineheart \
  --lookback-hours 720 \
  --backfill \
  --dry-run
```

预期：

- 官方 RSS：`source_unavailable/missing_secret`。
- sitemap：继续发现官网文章。
- xInfinite：继续发现通过微信身份校验的机器之心文章。
- 整体可能是 `degraded/coverage_incomplete`，但健康来源的候选和摘要仍保留。

若已有 Token，只在当前进程环境注入：

```bash
export MACHINEHEART_RSS_TOKEN="runtime-secret"
```

不要把值写入 JSON、文档、命令历史、报告或 Git。带 Token 的请求禁止跨 origin 重定向，错误只保留
origin/path。

## 三、正文与摘要

私人配置允许 sitemap 文章尝试正文，但最多受以下两层预算约束：

- `max_content_fetches`
- `max_content_fetches_per_target`

机器之心官网文章可能返回 HTTP 200 的数据服务提示页；质量门禁会标记
`publisher_data_service_gate`，不会把提示页总结成文章。

xInfinite 的 feed description 可能包含微信原文和来源摘要：

1. 必须恰好有一个 `mp.weixin.qq.com` 原文 URL。
2. 原文 `biz` 必须匹配目标。
3. `expected_author` 必须是“机器之心”。
4. 通过后 feed URL 保存为 `source_url`，微信 URL 保存为主 URL。
5. 微信正文被 CAPTCHA 阻断时，只能使用 feed 明确提供的摘要，并标记
   `summary_basis=source_summary`。
6. 发布时间超过发现时间一小时的条目被拒绝；若当前条目全部如此，来源标记为异常而不是零更新。
   不通过猜测固定时区去改写发布时间。

## 四、加入批量监控

复制 `assets/targets.batch.private.example.json`，补全其它 target 的身份与来源，再把目标
`enabled` 改为 `true`。私人模式不会自动启用停用 target。

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.batch.private.json \
  --state /absolute/path/to/batch.sqlite \
  --output /absolute/path/to/batch-output \
  --priority critical \
  --priority normal
```

单个 RSS 缺 Token、返回 CAPTCHA 或 schema 变化时，其它 target 继续运行。报告必须保留失败来源，
不能把局部失败写成“全部没有更新”。

## 五、定时监控

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" cron \
  --config /absolute/path/to/targets.machineheart.private.json \
  --state /absolute/path/to/machineheart.sqlite \
  --output /absolute/path/to/machineheart-output \
  --target machineheart \
  --schedule "17 * * * *"
```

如果使用标准资产临时覆盖，加入 `--private-use`；生成的运行命令会保留该参数。命令只打印，不自动
安装。上线前检查运行锁、失败告警、输出保留、Token 环境注入和日志容量。

## 六、验证

```bash
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py"
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py" --live-private
```

没有 `MACHINEHEART_RSS_TOKEN` 时，私人 live harness 把官方 RSS 的 `missing_secret` 视为预期技术
状态；xInfinite 必须实际返回至少一条通过 `biz` 校验的机器之心文章。公网结构变化或没有符合条目的
结果应报告为 degraded，不伪造成功。
