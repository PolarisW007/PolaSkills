# Devlog：pola-wechat-record-export V2

## 改动

- 将原技能的非私密脚本复制到版本化源码目录，明确排除 `wechat_keys_*.json`。
- 新增 `export_unified.py`、合成 Harness、统一记录 schema、README 与 Agent 元数据。
- 更新 SKILL，覆盖单聊、群聊、朋友圈和完整工作流。

## 稳定性与安全门禁

- 风险 P2。
- 只读 SQLite；记录/正文硬限制；无后台任务、无网络上传、无生产变更。
- Harness 只用临时合成数据。
- Python 编译、合成 Harness 7/7、skill validator、YAML、secret scan、`git diff --check` 均通过。
- Codex 已链接到版本化源码；旧技能整体迁入 `~/.local/share/pola-wechat-record-export/legacy-skill-20260725`，目录为 0700。
- 旧目录中的 2 个 root-owned key JSON 未读取、未复制、未提交；源码 key 文件计数为 0。
- GitHub 分支 `agent/pola-wechat-record-export-v2` 已推送；首个 commit：
  `c7d10e51d05c958cc30bc10dda9a731572a681fe`。
- Draft PR：`https://github.com/PolarisW007/PolaSkills/pull/1`，目标 `main`。

## 不影响功能使用

原 TXT、会话筛选、日期筛选、断点和语音转写入口保持；微信应用、登录态、原始数据库和旧导出均不修改。
