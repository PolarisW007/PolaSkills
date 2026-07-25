# PolaSkills

Pola 系列 Agent Skills 发布仓库。当前公开：

- [`pola-wechat-public-account-reader`](pola-wechat-public-account-reader/README.md)：通过纯公网来源批量
  监控微信公众号及其它媒体，支持 RSS/Atom、微信公众号公开 Album/Homepage、sitemap、已知文章
  URL 和远程 JSON API，并输出增量状态、来源健康、可信摘要素材和 JSON/Markdown 报告。
- [`pola-wechat-record-export-v2`](pola-wechat-record-export-v2/README.md)：只读导出本机微信单聊、群聊和
  本机已缓存朋友圈，生成统一 Markdown 时间线、NDJSON、CSV 与完整性 manifest。

## 快速安装

```bash
git clone https://github.com/PolarisW007/PolaSkills.git
cd PolaSkills/pola-wechat-public-account-reader

python3 scripts/run_harness.py
python3 scripts/install_hosts.py --host codex --apply
```

安装器默认只检查；只有显式传入 `--apply` 才会创建 Skill 链接。Claude Code、Qoder 和 Qoder Work
的安装位置与限制见
[`references/host-compatibility.md`](pola-wechat-public-account-reader/references/host-compatibility.md)。

本机微信记录导出技能可直接链接安装：

```bash
ln -s "$PWD/pola-wechat-record-export-v2" ~/.codex/skills/pola-wechat-record-export
python3 pola-wechat-record-export-v2/scripts/run_unified_harness.py
```

## 使用入口

- GitHub 使用说明：[`README.md`](pola-wechat-public-account-reader/README.md)
- Agent Skill 指令：[`SKILL.md`](pola-wechat-public-account-reader/SKILL.md)
- 配置字段：[`configuration.md`](pola-wechat-public-account-reader/references/configuration.md)
- 能力与案例：
  [`capabilities-and-examples.md`](pola-wechat-public-account-reader/references/capabilities-and-examples.md)
- 运行与定时化：[`operations.md`](pola-wechat-public-account-reader/references/operations.md)
- 本机记录导出说明：[`README.md`](pola-wechat-record-export-v2/README.md)
- 本机记录技能入口：[`SKILL.md`](pola-wechat-record-export-v2/SKILL.md)
- 统一记录字段：[`record-schema.md`](pola-wechat-record-export-v2/references/record-schema.md)

## 安全说明

不要把真实 Token、Cookie 或账号凭据写入配置、报告或 Git。需要鉴权的 provider 只通过运行环境
变量注入；私人来源模式不会绕过服务端 Token、验证码、登录页或正文质量门禁。

本机微信记录技能同样不收录任何 key、数据库或真实聊天数据；朋友圈结果只代表本机已缓存且当前
账号可见的内容。
