# pola-wechat-record-export-v2

面向 Codex、Claude Code、Cursor、Qoder 等 AgentSkills 兼容客户端的本机微信记录导出技能。
GitHub 发布目录使用 V2 名称；Agent Skill 内部调用名保持为 `$pola-wechat-record-export`。

## 主要能力

- 跨分片读取微信单聊和群聊。
- 读取已解密 `sns.db` 中本机缓存的朋友圈。
- 同时生成 Markdown 时间线、NDJSON、CSV 与完整性 manifest。
- 支持日期范围、身份哈希、记录/正文大小上限。
- 保留原有 TXT 导出、会话筛选、断点和语音转写能力。

## 快速使用

```bash
python3 scripts/export_unified.py \
  --decrypted-dir /private/path/decrypted_1 \
  --sns-db /private/path/decrypted_1/sns.db \
  --identity-mode hash \
  --output /private/path/export
```

```bash
python3 scripts/run_unified_harness.py
```

朋友圈结果只代表本机已缓存且当前账号可见的内容。技能、示例和 Harness 不包含任何真实聊天、数据库、密钥或 cookie。完整步骤见 [SKILL.md](SKILL.md)。
