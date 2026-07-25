---
name: pola-wechat-public-account-reader
description: 通过纯公网来源批量监控指定微信公众号及其它媒体的文章更新，读取可验证正文并生成中文摘要、情报简报和来源健康报告。用于维护和筛选多个公众号目标、查询某公众号近一周或近一月文章、监控机器之心、生成定时监控配置、执行增量抓取、排查公众号监控盲区，或把微信公众号 Album/Homepage、发布方 RSS/Atom 与 sitemap、已知文章 URL 和远程 JSON API 统一成监控任务。用户明确要求私人使用、忽略来源许可检查或打开 RSS/第三方来源时，使用显式 private_opt_in 模式；始终不依赖本机微信 App、扫码登录、Cookie、微信数据库或客户端内部接口。
---

# Pola 微信公众号公网阅读与监控

## 能力总览

- 在同一份 `targets[]` 配置中批量管理最多 100 个公众号或媒体目标。
- 默认运行全部启用目标，也可重复使用 `--target`、`--exclude-target`、`--priority` 精确缩小本轮范围。
- 聚合 Album、Homepage、RSS/Atom、发布方 sitemap、已知文章 URL 和可选 JSON API；按目标隔离失败并跨来源去重。
- 通过 `biz`、公网 HTTPS 和正文质量门禁校验文章；正文不可用时只使用来源明确提供的短摘要。
- 以 SQLite checkpoint 实现定时增量监控，输出 JSON/Markdown 报告、来源健康和覆盖盲区。
- 对批量正文读取设置全局和逐目标预算；先处理 `critical`，同优先级目标轮询，避免单个大号耗尽预算。
- 提供 `enforced` 和 `private_opt_in` 双来源策略；私人模式只启用显式标记的来源并记录 override。
- 同一份 Skill 可供 Codex、Claude Code、Qoder 和 Qoder Work 使用。

完整能力、限制和可复制案例见 [capabilities-and-examples.md](references/capabilities-and-examples.md)。

## 核心原则

把“发现文章”和“读取正文”作为两个独立问题处理。来源许可策略可以按私人用途放宽，技术安全、
服务端鉴权和内容真实性门禁不可关闭。

严格遵守：

- 不读取本机微信 App、数据库、内存、Cookie、Token 或扫码会话。
- 不绕过验证码、登录、频率限制或访问控制。
- 不把来源失败、验证码或不完整来源的零结果写成“公众号没有更新”。
- 用 `biz` 校验微信文章归属；名称和 `wxid` 只用于人类识别和检索。
- 只摘要通过正文质量门禁的内容；保留原文链接和不确定性。
- provider secret 只从环境变量读取；鉴权请求跨 origin 重定向必须阻断。
- 不自动创建生产 cron、发送消息或消费付费 API，除非用户明确要求。

## 来源策略模式

- `enforced`：默认兼容模式。启用且 `permission_required=true` 的来源需要非敏感
  `permission_reference`。
- `private_opt_in`：私人监控模式。不检查空 `permission_reference`，并启用
  `enable_in_private_mode=true` 的 source；不会重新启用停用 target 或其它普通停用 source。
- `fetch_content_in_private_mode=true` 允许私人模式尝试正文，但仍受正文预算、CAPTCHA、登录页、
  `publisher_data_service_gate` 和身份校验限制。

配置可持久设置 `defaults.source_permission_policy=private_opt_in`；也可对 `validate`、`run`、`cron`
传入 `--private-use`。两种方式都会在 validate 和报告中显示最终策略与被放开的来源。

## 第一次使用

1. 定位本 Skill 目录并设置一个任务专用变量：

   ```bash
   WECHAT_READER_SKILL="/path/to/pola-wechat-public-account-reader"
   ```

2. 将 `assets/targets.batch.example.json` 复制到用户的工作目录；私人批量监控使用
   `assets/targets.batch.private.example.json`。只监控机器之心时可从
   `assets/targets.machineheart.json` 或三来源全开的
   `assets/targets.machineheart.private.json` 开始。不在 Skill 安装目录内保存运行配置或状态。
   配置必须是最大 1 MiB 的普通文件，不使用 FIFO、设备文件或动态管道。
3. 阅读 [configuration.md](references/configuration.md) 后填写真实 `biz` 和至少一个公开来源。批量配置时给每个 target 分配唯一 `id`。
4. 先验证配置：

   ```bash
   python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" validate \
     --config /absolute/path/to/targets.json
   ```

5. 执行 dry run。dry run 访问来源但不更新 SQLite：

   ```bash
   python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
     --config /absolute/path/to/targets.json \
     --state /absolute/path/to/state.sqlite \
     --output /absolute/path/to/output \
     --dry-run
   ```

6. 检查报告里的 `coverage`、`targets[].sources[].status`、`identity_status` 和
   `content_status`；确认没有意外来源失败，并理解、接受报告声明的 coverage 盲区后再正式运行。

在多个宿主中共享这份 Skill，或检查 Codex 安装状态、修复断链时，阅读
[host-compatibility.md](references/host-compatibility.md)。安装器默认只检查，必须显式传入
`--apply` 才会写入宿主目录。

## 任务决策

### 查询单个公众号近一周文章

1. 查找并验证该公众号的 `biz`。
2. 优先寻找公开 Album URL 或 Homepage URL，从 URL 提取 `album_id` 或 `hid`。
3. 没有微信列表入口时，添加发布方官网 RSS/Atom；公开搜索只用于人工补漏，不作为完整性证据。
4. 将 `lookback_hours` 设为 `168`；首次查询历史或明确回补时同时传入 `--backfill` 后运行。
5. 如果所有来源不可用，回答“当前来源无法证明近一周完整文章清单”，不要回答“没有文章”。

### 建立目标列表监控

1. 为每个目标配置稳定内部 `id`、`name`、`wxid`、`biz` 和来源。
2. 无过滤参数时一次运行全部启用目标；需要拆分时重复使用 `--target`、`--exclude-target` 或 `--priority`。
3. 先用免费来源运行 7–14 天观察。
4. 对关键公众号配置两个真正独立的来源组；同一发布方的 RSS 和 sitemap 不是两个独立上游。来源选择和商业升级条件见 [source-strategy.md](references/source-strategy.md)。
5. 根据报告生成中文摘要和情报意义。
6. 验证单目标失败不会取消其它目标，且重复运行不会重复报告文章，再生成定时任务。

批量运行示例：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --priority critical \
  --priority normal \
  --exclude-target temporarily-paused
```

只运行两个指定目标时重复传入 `--target`。过滤器只会缩小启用目标集合，不能重新启用
`enabled=false` 的目标；未知 ID、显式选择停用目标或空结果会在联网前失败。

### 监控机器之心

使用内置身份：

```yaml
id: machineheart
name: 机器之心
wxid: almosthuman2014
biz: MzA3MzI4MjgzMw==
```

标准配置只运行官网 gzip sitemap。私人配置同时有效启用：

1. 官网 sitemap，并在正文预算内尝试官网文章正文。
2. 官方 RSS；服务端仍要求 `MACHINEHEART_RSS_TOKEN`，缺失时只将该来源标为
   `missing_secret`。
3. xInfinite RSS；只保留 description 中唯一微信原文、作者“机器之心”和目标 `biz` 均匹配的
   条目，并以一小时未来时间容差跳过明显的 feed 时钟异常。

RSS、sitemap 和第三方 feed 全部开启后 coverage 仍是 `partial`，不能表述为“公众号近一月完整清单”。

机器之心官网文章对自动客户端可能返回 HTTP 200 的数据服务提示页，而不是正文；质量门禁会把它
识别为 `publisher_data_service_gate`。私人模式也不会把提示页当正文。官方 RSS 的 Token 是服务端
技术鉴权条件；Skill 不生成或绕过 Token。

可复制配置和近 30 天运行命令见
[capabilities-and-examples.md](references/capabilities-and-examples.md#案例二机器之心近-30-天监控)。
更完整的标准/私人配置、RSS 和定时化步骤见
[machineheart-monitoring.md](references/cases/machineheart-monitoring.md)。

### 读取一个已知文章 URL

使用 `article_url` 来源或直接把 URL 加入目标配置。已知 URL 只能读取和校验该文章，不能发现后续更新。

正文出现 `blocked` 时保留标题、链接和来源摘要，不根据标题虚构全文摘要。

### 接入其它媒体

优先配置其 RSS/Atom。没有标准 feed 且用户提供 JSON API 时，使用 `json_api` adapter；字段映射见 [configuration.md](references/configuration.md)。

## 来源优先级

按顺序选择：

1. `wechat_album`：免费、无 Cookie；只覆盖运营方加入该合集的文章。
2. `wechat_homepage`：免费、无 Cookie；只覆盖公开主页模板或栏目。
3. `rss`：发布方官网、独立博客、自托管 RSSHub 或私人模式第三方 feed。
4. `sitemap`：发布方官网文章缺口审计；它不是公众号完整历史，默认不逐篇抓正文。
5. `article_url`：读取已知文章，不承担更新发现。
6. `json_api`：可选远程 provider；未经 POC 不作为唯一生产源。

不要把多个包装同一上游的 RSS 地址，或同一发布方的 RSS 与 sitemap，计为两个独立来源。
标准模式对需要许可引用的来源执行门禁；私人模式可显式放宽，但必须保留来源标识、失败和
`partial` coverage，不把多个包装同一上游的 URL 伪装成独立覆盖。

## 运行与产物

正式运行：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output
```

只监控指定目标：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" run \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --target machineheart \
  --lookback-hours 720 \
  --backfill
```

脚本输出：

- `report-<run_id>.json`：机器可读证据。
- `report-<run_id>.md`：人类可读情报报告。
- `latest.json`、`latest.md`：最近一次结果。
- SQLite：按目标隔离的文章去重、运行和来源 observation；运行证据按保留期清理。

脚本会生成可定时运行的确定性摘录摘要：有效正文使用前三个信息句，正文不可用时只使用来源明确提供的摘要，并在 `summary_basis` 标注依据。Codex 再将其升级为语义摘要。

按字段解释运行结果，不要把不同层级混用：

- `targets[].sources[].status`：`ok`、`observed_zero`、`source_unavailable` 或 `blocked`。
- `targets[].status`：`new_articles`、`observed_zero`、`no_new_articles_observed`、
  `partial_observation` 或 `source_unavailable`。
- 顶层 `result=new_articles`：至少发现一篇首次出现的文章。
- 顶层 `result=no_new_articles_observed`：全部选中目标来源健康且没有新增；既可能窗口内无候选，
  也可能候选均已见过。
- 顶层 `result=coverage_incomplete`：至少一个选中目标存在来源失败或未知覆盖；即使其它目标健康且
  无新增，也不能断言全部无更新。
- `coverage=partial`：至少一个来源健康，但没有完整性承诺；`coverage=unknown` 表示所有来源均不健康。
- `content_status=blocked` 且 `content_reason=publisher_data_service_gate`：正文 URL 返回发布方数据
  服务引导页；按正文阻断处理，不绕过。

默认免费来源永远不要升级为 `complete`。

## 生成摘要

读取 JSON 报告的 `new_articles` 和自动 `summary`，只深化处理：

- `content_status=valid` 的 `summary_input`；或
- `content_status=blocked/unavailable` 且 `summary_basis=source_summary` 的文章。

每篇生成：

1. 一句话事实摘要。
2. 3–5 条核心要点。
3. 一句“为何值得监控”。
4. 原文链接、发布时间、公众号和 coverage。
5. 无法由正文证实的内容标注“仅据来源摘要/标题”。

不要大段复制全文。摘要失败时仍输出标题、时间和原文链接。

## 定时任务

先阅读 [operations.md](references/operations.md)。生成 cron 示例：

```bash
python3 "$WECHAT_READER_SKILL/scripts/wechat_monitor.py" cron \
  --config /absolute/path/to/targets.json \
  --state /absolute/path/to/state.sqlite \
  --output /absolute/path/to/output \
  --schedule "17 * * * *"
```

部署前确认：

- 正常目标每 60 分钟、关键目标每 15 分钟，加入时间抖动。
- 实时运行回看 72 小时；夜间回补 7 天。
- 运行锁有效；重复任务会明确退出。
- 请求和整轮运行都有 deadline、最大响应字节和有限分页。
- 目标、来源、候选和正文抓取数量均有上限。
- 正文预算同时受全局 `max_content_fetches` 和逐目标
  `max_content_fetches_per_target` 限制；未获预算的文章仍可保留来源摘要。
- 输出报告和 SQLite 运行证据有保留天数，日志不会无界增长。
- `source_unavailable` 会触发盲区告警。

## 验证

离线 harness 必须通过：

```bash
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py"
```

显式执行公网 smoke test：

```bash
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py" --live
```

显式测试私人模式机器之心 RSS：

```bash
python3 "$WECHAT_READER_SKILL/scripts/run_harness.py" --live-private
```

没有 `MACHINEHEART_RSS_TOKEN` 时，官方 RSS 的预期结果是 `missing_secret`；xInfinite 必须能独立
通过作者、唯一微信原文和 `biz` 校验，才算私人公网 smoke 通过。

退出码：

- `0`：离线测试通过；使用 `--live` 时公网样例也通过。
- `1`：代码或断言失败。
- `2`：离线测试通过，但公网来源当前被阻断或不可用。

联网退化不是“没有更新”。记录具体来源、HTTP/错误类型和时间。

## 监控目标示例

`instachina` 已知稳定身份：

```yaml
id: instachina
name: InsDaily
wxid: instachina
biz: MzI2MTcxMjI0MQ==
```

仅有 `biz` 仍不足以通过免费公开接口枚举全部历史文章。没有确认的 `album_id`、`hid`、官网 RSS 或远程 provider 时，将该目标标记为未配置，不伪造来源。

## 宿主兼容

`SKILL.md`、`scripts/`、`references/` 和 `assets/` 只维护一份规范源码。用以下命令检查四个宿主：

```bash
python3 "$WECHAT_READER_SKILL/scripts/install_hosts.py"
```

按宿主显式安装缺失链接：

```bash
python3 "$WECHAT_READER_SKILL/scripts/install_hosts.py" \
  --host codex \
  --apply
```

支持的宿主 ID、默认目录、断链修复、安全拒绝和退出码见
[host-compatibility.md](references/host-compatibility.md)。Qoder 和 Qoder Work 的符号链接发现能力需在对应运行时做 smoke test。

## 来源策略与技术安全门禁

- 用户明确选择私人模式时，不核验 robots、服务条款或 `permission_reference`，但在报告中记录
  策略和 override。
- 标准模式继续保留原权限引用门禁，确保旧配置和旧定时任务不静默改变。
- 不自建微信账号池，不模拟客户端内部接口，不轮换代理对抗封控。
- API key 只通过环境变量注入；禁止写入配置、日志、报告或 Git。
- env header 只接受有界可打印 ASCII；非法值必须以脱敏错误拒绝。
- HTTPS 连接固定到本轮已校验的公网 DNS 地址；不允许鉴权 header 跨 origin 跟随重定向。
- 登录、CAPTCHA、401/403、付费 Token 和数据服务提示页仍是技术阻断；私人模式不绕过。
- 请求、响应、gzip、分页、候选、正文、运行时间、锁和报告保留均保持有界。
- cron 路径不得包含 CR、LF、NUL 或 `%`。
- 生产启用、真实消息外发和商业消费需要用户明确确认。
