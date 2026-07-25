# 能力、使用方式与案例

## 这项技能能做什么

| 能力 | 输入 | 输出 | 可信边界 |
| --- | --- | --- | --- |
| 批量监控 | 一份含多个 `targets[]` 的 JSON | 逐目标新增、失败、coverage 和汇总 | 最多 100 个 target；来源失败互相隔离 |
| 增量去重 | 同一配置和 SQLite 状态 | 只报告首次见到的文章 | 首次运行默认建基线；回补需显式 `--backfill` |
| 定向运行 | 重复的 target/排除/priority 过滤器 | 本轮 `selection` 证据 | 过滤只缩小启用集合 |
| 多源发现 | Album、Homepage、RSS、sitemap、已知 URL、JSON API | 统一文章候选 | 多个 URL 不一定是独立上游 |
| 身份校验 | target `biz`、文章 URL 和来源绑定 | `verified`、`source_bound` 或 `unverified` | `wxid` 和名称不代替 `biz` |
| 正文与摘要 | 公网文章页或来源摘要 | 正文状态、摘要依据和中文简报素材 | CAPTCHA 时不虚构全文 |
| 来源健康 | 每次 source observation | `ok`、`observed_zero`、`blocked`、`source_unavailable` | 来源失败不能解释为没有更新 |
| 私人来源模式 | `private_opt_in` 或 `--private-use` | 打开显式标记的 RSS/正文尝试和 override 证据 | 不绕过服务端鉴权或技术安全 |
| 定时化 | 已审阅配置和路径 | 安全的 cron 示例 | 只打印，不自动安装或外发 |
| 四宿主目录兼容 | 一份规范 Skill 目录 | 检查/安装 Codex、Claude Code、Qoder、Qoder Work 用户目录 | 安装器不覆盖真实目录或异源链接；安装后仍需宿主 smoke |

这项技能不保证任意第三方公众号的免费、零登录、完整历史；不使用本机微信 App、扫码、Cookie、
客户端数据库或内部接口；不绕过验证码和访问控制。

## 标准使用方式

```bash
WECHAT_READER_SKILL="/absolute/path/to/pola-wechat-public-account-reader"

python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" validate \
  --config /absolute/path/to/targets.json

python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --dry-run

python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output
```

第一次先 dry run，确认 target 身份、最终来源策略和 coverage；再正式运行建立 checkpoint。运行配置、
SQLite 和报告应放在用户工作目录，不放进 Skill 安装目录。

私人模式可直接复制 `assets/targets.batch.private.example.json`，或在任意三条命令后加入
`--private-use`。它只启用 `enable_in_private_mode=true` 的来源，不重新启用停用 target。

## 案例一：批量监控多个目标

一份配置中的 `targets` 是批量列表。下例展示三种状态：关键目标、普通目标和暂未配置完成的停用目标。
可直接复制 [targets.batch.example.json](../assets/targets.batch.example.json) 后修改。

```json
{
  "version": 1,
  "defaults": {
    "lookback_hours": 72,
    "max_candidates_per_target": 1000,
    "max_total_candidates": 5000,
    "max_content_fetches": 10,
    "max_content_fetches_per_target": 3,
    "baseline_mode": "from_now"
  },
  "targets": [
    {
      "id": "publisher-a",
      "name": "关键媒体 A",
      "priority": "critical",
      "enabled": true,
      "sources": [
        {
          "type": "rss",
          "url": "https://publisher-a.example/feed.xml",
          "independence_group": "publisher-a",
          "enabled": true
        }
      ]
    },
    {
      "id": "publisher-b",
      "name": "普通媒体 B",
      "priority": "normal",
      "enabled": true,
      "sources": [
        {
          "type": "sitemap",
          "url": "https://publisher-b.example/sitemap.xml.gz",
          "url_pattern": "^https://publisher-b\\.example/articles/",
          "fetch_content": false,
          "independence_group": "publisher-b",
          "enabled": true
        }
      ]
    },
    {
      "id": "pending-account",
      "name": "待确认公众号",
      "priority": "low",
      "enabled": false,
      "sources": []
    }
  ]
}
```

运行全部启用目标：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output
```

只运行两个 ID：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --target publisher-a \
  --target publisher-b
```

运行关键与普通目标，但排除一个临时目标：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --priority critical \
  --priority normal \
  --exclude-target publisher-b
```

同优先级 target 轮询分配正文预算；某一 target 来源失败时，其它 target 继续并写入报告。批量结果必须
阅读 `selection`、`targets` 和 `errors`，不能只看新增总数。

## 案例二：机器之心近 30 天监控

### 标准路径

机器之心身份：

```yaml
id: machineheart
name: 机器之心
wxid: almosthuman2014
biz: MzA3MzI4MjgzMw==
```

标准模式只使用
`https://www.jiqizhixin.com/shared/sitemap.xml.gz`。完整 JSON 见
[targets.machineheart.json](../assets/targets.machineheart.json)，字段说明见
[configuration.md](configuration.md#机器之心配置)。把样例复制到工作目录后，先验证，再显式回补
720 小时：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" validate \
  --config /absolute/path/to/machineheart.json

python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/machineheart.json \
  --state /absolute/path/to/machineheart.sqlite \
  --output /absolute/path/to/machineheart-output \
  --target machineheart \
  --lookback-hours 720 \
  --backfill \
  --dry-run
```

确认候选和日期后去掉 `--dry-run`。sitemap 结果只代表机器之心官网文章，coverage=`partial`。
标准模式保持 `fetch_content=false`。正确表述是“官网 sitemap 在近 30 天观察到 N 个候选”，不是
“机器之心公众号近 30 天共有 N 篇”。

内置示例最多保留 sitemap 的 1000 个最新候选。扩大回看窗口前先检查最旧候选日期、来源是否
截断和本轮安全预算；提高上限也不会把 `partial` 升级为完整公众号历史。

机器之心官网文章对自动客户端可能返回 HTTP 200 的数据服务提示页，而不是正文；解析器会以
`publisher_data_service_gate` 阻断。私人模式可以尝试正文，但仍不会把提示页形成摘要。

### 私人三来源全开

复制 `assets/targets.machineheart.private.json`，即可有效开启官网 sitemap、官方 RSS 和 xInfinite。
也可以继续使用标准资产并传入 `--private-use`。官方 RSS 的 Token 是服务端技术条件；有值时只在
运行环境设置：

```bash
export MACHINEHEART_RSS_TOKEN="runtime-secret"

python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/machineheart.private.json \
  --state /absolute/path/to/machineheart.sqlite \
  --output /absolute/path/to/machineheart-output \
  --target machineheart \
  --private-use
```

没有 Token 时官方 RSS 报告 `missing_secret`，sitemap 与 xInfinite 继续。xInfinite 内置
`entry_link_mode=wechat_original_from_description` 和 `expected_author=机器之心`，只接收唯一微信
原文和 `biz` 均匹配的条目。若微信正文返回 CAPTCHA，文章仍保留标题、原文和发现链接，只使用 RSS
明确提供的短摘要。

此外，用户也可以提供一个已知、当时可直接公开访问的微信原文 URL，使用
`article_url` 做单篇正文质量验证；它不能承担后续更新发现。

## 案例三：读取已知公众号文章

已知 URL 适合单篇内容验证，不适合发现后续更新：

```json
{
  "id": "known-article-check",
  "name": "已知文章校验",
  "biz": "MzExampleBiz==",
  "priority": "normal",
  "enabled": true,
  "sources": [
    {
      "type": "article_url",
      "url": "https://mp.weixin.qq.com/s?__biz=MzExampleBiz%3D%3D&mid=1&idx=1&sn=abc",
      "enabled": true
    }
  ]
}
```

`__biz` 与 target 不一致时配置或候选校验失败。正文通过质量门禁时摘要依据是 `full_text`；被阻断时
不把验证码页面当正文，也不根据标题补写事实。

## 案例四：生成定时监控命令

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" cron \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --schedule "17 * * * *" \
  --priority critical
```

命令只打印一行 cron，不安装。关键目标可约每 15 分钟，普通目标约每 60 分钟，sitemap 建议每日
或每周；频率过高会增加 429、封禁和资源占用。调度策略、锁、退避和日志保留见
[operations.md](operations.md)。

## 案例五：正确解释局部失败

假设批量中：

- A 的 RSS 健康，窗口内 0 篇；
- B 的 Homepage 被 CAPTCHA；
- C 的 sitemap 发现 2 篇，但都已见过。

不能写“本轮所有目标没有更新”。正确结论是：

> A 与 C 的已配置公网来源本轮未观察到新文章；B 存在监控盲区。整体覆盖不完整，无法证明所有
> 目标均无更新。

报告中应看到 B 的 `blocked` 或 `source_unavailable`、整体
`coverage_incomplete`，以及每个 target 的独立状态。只有所有选中目标的来源健康时，
`no_new_articles_observed` 才是合适的批量结果。

## 案例六：在四个宿主使用同一份 Skill

先检查，不修改：

```bash
python3 "$WECHAT_READER_SKILL/scripts/install_hosts.py"
```

只安装缺失的 Codex 链接：

```bash
python3 "$WECHAT_READER_SKILL/scripts/install_hosts.py" \
  --host codex \
  --apply
```

可重复 `--host codex`、`--host claude-code`、`--host qoder`、
`--host qoder-work`。断链修复需同时传入 `--apply --repair-broken`；真实目录、普通文件或指向其它
有效来源的链接不会被覆盖。四个宿主目录与 smoke test 见
[host-compatibility.md](host-compatibility.md)。

安装后从宿主可见路径运行：

```bash
python3 /absolute/host/skills/pola-wechat-public-account-reader/scripts/run_harness.py
```

Qoder 和 Qoder Work 的公共文档未保证所有运行时都跟随符号链接，因此安装后必须实际触发一次
Skill 并运行离线 harness。

## 摘要交付格式

对每篇新增文章输出：

1. 一句话事实摘要。
2. 3–5 条核心要点。
3. 一句“为何值得监控”。
4. 原文 URL、发现来源 URL、发布时间、target 和 coverage。
5. `summary_basis=source_summary` 时明确标注“仅据来源摘要”；没有可信正文或来源摘要时只给元数据。

不要大段复制原文。确定性 runtime 摘要只为定时任务提供基础材料；Agent 可以深化表达，但不得补写
`summary_input` 无法支持的事实。

## 验证

```bash
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py"
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py" --live
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py" --live-private
```

离线 harness 验证配置、解析、批量、secret、报告、SQLite 和宿主安装。`--live-private` 显式测试
xInfinite；没有机器之心 Token 时，官方 RSS 的预期状态是 `missing_secret`。公网退化退出码 2 表示
来源当前不可用，不等于没有文章。
