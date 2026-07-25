# SDD：pola-wechat-record-export V2

## 风险

P2：涉及本机数据库、私人内容、大批量读取和可能的密钥。不存在服务端、数据库迁移或生产重启。

## 数据流

```text
离线解密 message_*.db / biz_message_*.db ─┐
                                           ├─ 只读适配器 ─ 统一事件 ─ NDJSON/CSV/Markdown/manifest
离线解密 sns.db / SnsTimeLine ────────────┘
```

## 设计

- SQLite URI `mode=ro&immutable=1` + `query_only=ON`。
- 跨分片以 `conversation + database + local_id` 构造来源，再生成稳定 SHA-256 record id。
- 统一 `stream/occurred_at/author/conversation/content_type/text/source`。
- `--identity-mode hash` 为作者/会话生成 SHA-256。
- 默认 250,000 条、单正文 2 MiB；参数有硬上限。
- manifest 保存参数、分类计数、时间范围和输出文件 SHA-256。
- 朋友圈每条都带 `local_cache_incomplete=true` 和 freshness warning。

## 兼容与回滚

旧 `export_all.py` 路径保持不变；删除新入口即可回滚。技能安装使用符号链接，不复制私密 key。无生产发布。

## 测试策略

使用临时合成 SQLite 数据库，不读取真实聊天或 key；覆盖三类 stream、格式、身份哈希、缓存警告、完整性及 secret 文件排除。
