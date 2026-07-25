# Requirement：pola-wechat-record-export V2

## 原始需求

基于 PolaLuna 与 PolaGithubRepoResearch 的微信读取研究升级技能，覆盖单聊、群聊、朋友圈；优化记录格式，完成 Codex 安装、Harness，并提交到 `PolarisW007/PolaSkills`。

## 目标与边界

- 从用户本人有权访问的本机离线解密副本读取数据。
- 合并多消息分片，朋友圈明确限定为本机缓存。
- 同时提供适合阅读和数据处理的输出。
- 不提交或回显 key、cookie、原始数据库、聊天正文。
- 不修改微信应用，不执行生产发布，不承诺云端完整历史。

## 验收标准

1. 合成 Harness 同时产生 `private/group/moments`。
2. 输出 Markdown、NDJSON、CSV、manifest，哈希可校验。
3. 有记录数、正文大小上限及只读连接。
4. Codex 安装指向版本化源码，旧私密 key 文件得到安全保留。
5. GitHub 子目录及使用说明可读取。

## 追加发布要求

- GitHub 仓库中的目录名称必须精确为 `pola-wechat-record-export-v2/`。
- 技能内部名称继续使用 `pola-wechat-record-export`，保证现有 Codex 调用兼容。
