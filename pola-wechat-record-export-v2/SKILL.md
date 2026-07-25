---
name: pola-wechat-record-export
description: 在 macOS 本机以只读方式导出个人微信数据，覆盖单聊、群聊和本机已缓存朋友圈；支持多账号、多消息分片、时间/会话筛选、断点导出、语音转写，以及统一 Markdown、NDJSON、CSV、manifest 输出。用户提到微信聊天记录、群聊记录、朋友圈、sns.db、WeChat SQLCipher、本地微信数据导出或后续摘要/数据分析时使用。
---

# Pola 微信记录导出

把用户本人有权访问的本机微信数据导出成既能阅读、又能被其他程序稳定处理的记录。全程优先使用离线副本和只读 SQLite 连接；不得上传原始数据、密钥或 cookie。

## 能力

- 单聊、群聊：跨 `message_*.db` 和 `biz_message_*.db` 合并同一会话，保留来源分片。
- 朋友圈：读取已解密 `sns.db` 的 `SnsTimeLine`，输出正文、媒体/链接计数和覆盖边界。
- 输出：`timeline.md`、`records.ndjson`、`records.csv`、`manifest.json`。
- 旧版兼容：保留 `export_all.py` 的 TXT 导出、会话筛选、日期筛选、断点恢复和可选语音转写。
- 隐私模式：`--identity-mode hash` 对作者和会话标识做 SHA-256；默认 `clear` 便于私人阅读。

## 重要边界

1. 朋友圈只代表本机已缓存且当前账号可见的内容。缺失不代表删除、屏蔽或权限变化。
2. 微信升级可能改变数据库路径或 schema。schema 不匹配时停止并报告，不猜测列含义。
3. 密钥只用于本机解密步骤。禁止把 `wechat_keys*.json`、数据库、聊天正文或导出目录放进 Git。
4. 不修改、重签名或注入微信应用；不为绕过系统保护关闭安全机制。若已有用户自行准备的离线解密副本，直接从副本开始。
5. 处理前先确认磁盘余量；默认最多 250,000 条记录，单条正文最多 2 MiB。需要扩大时显式设置。

## 推荐工作流

### 1. 发现并确认输入

先只读检查目标目录，不显示密钥内容：

```bash
find /path/to/decrypted -maxdepth 2 -type f \
  \( -name 'message_*.db' -o -name 'biz_message_*.db' -o -name 'sns.db' \) -print
```

如果仍是加密数据库，可使用本技能既有的 `decrypt_db.py`，但只传入用户明确提供的本机密钥文件；不要在命令行或日志里回显 key：

```bash
python3 scripts/decrypt_db.py --account 1 \
  --keys-file /private/path/wechat_keys.json \
  --output /private/path/decrypted_1
```

### 2. 先预览聊天范围

```bash
python3 scripts/export_all.py --account 1 \
  --decrypted-dir /private/path/decrypted_1 \
  --type all --start 2026-07-01 --end 2026-07-31 --dry-run
```

### 3. 生成统一格式

聊天 + 朋友圈：

```bash
python3 scripts/export_unified.py \
  --decrypted-dir /private/path/decrypted_1 \
  --sns-db /private/path/decrypted_1/sns.db \
  --start 2026-07-01 --end 2026-07-31 \
  --identity-mode clear \
  --output /private/path/exports/wechat-2026-07
```

只导出聊天或朋友圈时省略另一个输入。用于模型、BI 或跨系统处理时建议加 `--identity-mode hash`。

### 4. 验证结果

- 回读 `manifest.json`，确认三类计数和时间范围。
- 校验 `manifest.json.files[*].sha256`。
- 抽查 `timeline.md` 与 `records.ndjson` 的时间、作者、正文一致。
- 若包含朋友圈，必须在交付中保留缓存覆盖警告。
- 输出目录权限建议设为 `0700`，文件为 `0600`。

## 输出说明

```text
output/
├── timeline.md       # 按天排列的人类可读时间线
├── records.ndjson    # 一行一个稳定事件，适合 LLM/ETL/增量去重
├── records.csv       # Excel、Sheets、BI 可直接导入
└── manifest.json     # 参数、计数、时间范围、warning、文件哈希
```

统一字段见 [references/record-schema.md](references/record-schema.md)。

## 旧版精细聊天导出

按群聊、联系人或时间导出 TXT：

```bash
python3 scripts/export_all.py --account 1 \
  --decrypted-dir /private/path/decrypted_1 \
  --type group --contacts "工作群,项目群" \
  --start 2026-07-01 --end 2026-07-31 \
  --output /private/path/exports
```

规模较大时保留默认断点状态；只有用户明确要求从头生成时才使用 `--fresh`。语音转写会显著增加耗时和依赖，只在用户明确需要时使用 `--transcribe`。

## Harness

先运行不接触真实数据的合成 Harness：

```bash
python3 scripts/run_unified_harness.py
```

它验证单聊、群聊、朋友圈、缓存警告、哈希身份、NDJSON/CSV/Markdown 和文件完整性。已有完整的脱敏测试副本时，可再运行：

```bash
python3 scripts/run_export_harness.py \
  --decrypted-dir /private/path/fixture \
  --username fixture_user
```

## 研究依据

- PolaLuna：多消息分片只读游标；`private/group/moments/moment_interactions` 数据流；朋友圈 local-cache-only 与稳定来源元数据。
- PolaGithubRepoResearch：本机微信数据读取项目的适配与安全边界研究。
- Tencent WCDB：微信数据库基于 SQLite/SQLCipher 的技术基础。
- 社区项目只作为版本兼容参考；不得把其“可读”声明等同于当前微信版本已验证。

## 交付回答

报告输入范围、三类记录数、时间范围、输出路径、身份模式、覆盖 warning、失败/跳过项与 Harness 结果。不要在回答中复制大量私人正文。
