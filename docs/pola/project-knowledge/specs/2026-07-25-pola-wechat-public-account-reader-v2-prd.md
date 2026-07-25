# PRD：公众号公网情报监控 Skill V2

## 1. 产品目标

把已有单次监控工具升级为可管理一组公众号的批量情报入口，并把机器之心案例、来源合规和四宿主安装做成可复用能力。

产品继续承诺“可追溯的公网观测”，不承诺任意公众号的免费完整覆盖。

## 2. 用户场景

### 2.1 批量运行

1. 用户在同一配置中维护多个 target。
2. 系统离线校验所有 target/source，再访问网络。
3. 默认运行全部启用目标；用户可按 ID、排除项或优先级缩小范围。
4. 某目标失败时其它目标继续。
5. 报告按目标聚合，并在顶部明确更新、失败和未知目标数量。

### 2.2 机器之心增量监控

1. 免费默认路径低频读取 robots 公布的官方 sitemap，获得发布方网站文章元数据。
2. 免费 RSS 权益不用于 AI Agent；只有取得支持 Agent 的订阅或书面许可后才启用官方 RSS。
3. 配置通过 `query_from_env` 注入 token，不把 token 写进配置或报告。
4. RSS 提供可靠来源摘要或原文可读取时才生成摘要；被 CAPTCHA/数据服务引导页阻断时不绕过。
5. sitemap 结果标记为发布方网站内容，不伪装成公众号完整清单；URL slug 不作为正文摘要。

### 2.3 授权的账号镜像 RSS

1. 用户确认自动访问许可，并配置非敏感 `permission_reference`。
2. RSS description 中只能存在一个目标微信原文链接。
3. 系统提取原文标题、作者、链接和短摘要，校验 `biz`。
4. 发现页与原文页均保留，摘要标注正文或来源摘要依据。
5. 无授权引用时配置在联网前失败。

### 2.4 四宿主使用

1. 规范源码只保存在一个 Skill 目录。
2. 检查脚本展示 Codex、Claude Code、Qoder、Qoder Work 的安装状态。
3. 安装动作必须显式执行；默认只检查。
4. 已存在不同目录时拒绝覆盖；断链可在明确 repair 模式下修复。
5. 每个宿主从安装路径运行同一离线 harness。

## 3. CLI 行为

### `validate`

- 输出启用目标、停用目标、来源数和逐目标概要。
- 字符串 `"false"`/`"true"` 不是合法布尔值。
- 重复 source key、未知 source、私网 URL 和无授权受限来源立即失败。

### `run`

- `--target ID`：可重复，只包含指定启用目标。
- `--exclude-target ID`：可重复，从已选集合排除。
- `--priority critical|normal|low`：可重复。
- 未知 ID、显式选择 disabled 目标、过滤后为空时在联网前失败。

### 报告

- 顶部包含 `selected_targets`、`targets_with_updates`、`targets_with_failures`。
- 混合失败时不输出 `no_new_articles_observed`。
- Markdown 中对标题、名称和摘要做安全转义。

## 4. 内容与摘要

- `content_status=valid`：通过正文质量门禁。
- `summary_basis=full_text`：只来自有效正文。
- `summary_basis=source_summary`：只来自来源明确提供的摘要。
- `blocked`：保留元数据，不把验证码文字进入摘要。
- `publisher_data_service_gate`：发布方要求改用数据服务；不得把引导页当正文或尝试绕过。
- 文章保留：
  - `url`：主文章 URL。
  - `source_url`：发现/镜像来源 URL。
  - `original_url`：从授权来源内容中验证出的微信原文 URL。
  - `content_source_url`：实际用于正文读取的 URL。

## 5. 状态和空态

- 所有目标健康且无候选：`no_new_articles_observed`。
- 至少一个目标未知/失败且无新增：`coverage_incomplete`。
- 有新增：`new_articles`；同时仍保留局部失败。
- 所有目标未知：run failed，coverage=`unknown`。

## 6. 性能护栏

- 最多 100 个目标、每目标 10 个来源。
- 默认串行目标访问，不增加未经验证的并发。
- 每目标候选上限和全局候选上限同时生效。
- 正文抓取有全局预算和每目标预算。
- 分配顺序：`critical → normal → low`，同优先级目标轮询。
- sitemap gzip 解压后有独立字节上限。
- 所有请求、分页和整轮运行受 deadline 限制。

## 7. 安全与合规

- 候选、重定向和正文 URL 都必须是公网 HTTPS。
- query secret 只从环境变量读取；错误信息移除 query/fragment。
- 不允许 Cookie header。
- `permission_required=true` 的来源必须提供非敏感授权引用。
- robots 与服务条款都要检查；robots 未禁止不等于获得授权。
- 不自动创建生产定时任务或安装到未明确要求的宿主。

## 8. 回滚体验

- 新配置字段均为可选；删除即可恢复旧 RSS 行为。
- 四宿主安装使用链接/受控副本，删除链接即可回滚。
- SQLite 不做破坏性 schema 迁移。
- 机器之心来源可逐个 disabled，不影响其它 target。

## 9. 验收

以 V2 需求文档 V2-A1 至 V2-A12 为准。核心路径必须由离线 harness 和条款允许的公网 smoke 共同验证。
