# Pola WeChat Unified Record v1

每行 `records.ndjson` 是一个独立事件：

- `record_id`：来源稳定标识的 SHA-256，可用于去重。
- `stream`：`private`、`group` 或 `moments`。
- `occurred_at`：带时区的 RFC 3339 时间。
- `author`、`conversation`：明文或 `sha256:` 标识，取决于 `--identity-mode`。
- `content_type`、`text`：内容类型与正文。
- `source`：数据库名、表哈希、local id、原始类型及截断/覆盖元数据。
- `coverage_warning`：朋友圈的本机缓存边界。

`manifest.json` 保存总数、分类计数、时间范围、参数以及三个结果文件的 SHA-256。
`records.csv` 适合表格/BI，`timeline.md` 适合直接阅读和后续摘要。
