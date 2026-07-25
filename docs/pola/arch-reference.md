# Skills 仓库 Arch Reference

## 项目形态

- 本仓库是多个 Codex/Claude/Cursor Skill 及配套脚本的集合。
- 每个 Skill 以独立目录交付，核心入口为 `SKILL.md`。
- Codex UI 元数据使用 `agents/openai.yaml`。
- 确定性、重复执行或高风险流程优先放入 `scripts/`。
- 详细但非每次必读的知识放入 `references/`。

## 运行与依赖

- 仓库没有统一包管理器、构建系统或根级测试命令。
- 新 Skill 应尽量自包含；本次监控工具仅依赖 Python 3 标准库。
- Skill 基础结构使用 Codex `skill-creator` 的 `init_skill.py` 生成。
- Skill 元数据使用 `quick_validate.py` 校验。

## 数据与状态

- Skill 自身不保存凭证。
- 运行时配置、SQLite 状态、报告和缓存必须写入调用方指定的工作目录，不写入 Skill 安装目录。
- 配置示例可以进入版本库，真实 `config.json`、数据库和输出不进入版本库。

## 测试模式

- 使用可重复的离线 fixtures 验证解析、去重和状态语义。
- 联网 smoke test 只验证当前公网可达性，网络或上游阻断必须报告为 `source_unavailable`，不能伪装为通过或“无更新”。
- Skill 修改后运行 Codex Skill 校验、Python 编译、单元测试和端到端 harness。

## Skill 评测架构

- `pola-skill-eval` 使用“静态门禁 + baseline/candidate/old 配对执行 + 确定性 grader + 性能统计”的证据链。
- 动态评测默认 dry-run，只有显式 `--execute` 才运行外部 argv；禁止 shell 拼接。
- 每个 case/arm/trial 必须使用隔离工作区，并设置并发、超时和累计输出上限。
- 发布决策优先使用结构、安全和关键功能硬门禁；软评分不能覆盖致命失败。
- 效率同时观察静态上下文体积、p50/p95、输出字节，以及 runner trace 可用时的 token、成本和工具调用。
- Skill 评测报告必须区分 `pass`、`conditional`、`inconclusive`、`blocked` 与 `reject`，证据不足不能伪装为通过。

## 来源策略模式

- `enforced` 是旧配置和通用示例的默认值：需要许可引用的启用来源缺少引用时联网前失败。
- `private_opt_in` 供私人情报监控显式选择：只放宽来源许可引用，并只启用标记为
  `enable_in_private_mode=true` 的来源。
- 私人模式不会重新启用停用 target 或普通运维暂停来源，也不会把来源 completeness 自动提升。
- 两种模式共用同一 runtime、SQLite、报告和四宿主安装，不维护并行代码分支。

## 多宿主 Skill 事实

- Codex、Claude Code、Qoder 和 Qoder Work 均可消费 Agent Skills 结构的 `SKILL.md`。
- 核心 Skill 只维护一份；宿主目录使用符号链接、受控同步或插件打包引用。
- Codex 的 `agents/openai.yaml` 是可选宿主元数据，其它宿主可忽略。
- 宿主安装脚本默认只检查，不能静默覆盖已有真实目录或有效的不同链接。
- 发布包不能依赖指向包外的符号链接；打包时应物化同一规范源码。

## 复用优先级

1. 复用 Codex Skill 标准目录和元数据。
2. 复用 Pola A2A 的需求、架构、测试和开发日志门禁。
3. 公网发现优先使用无需登录的微信 Album/Homepage 和站点 RSS。
4. 需要更高召回率时，通过独立 adapter 接入远程商业 API，不将其硬编码为免费能力。

## 不可破坏的约束

- 不依赖本机微信 App、微信数据库、扫码登录、Cookie、Token、账号池或验证码绕过。
- 不把单一不完整来源的零结果声明成“公众号没有更新”。
- 不把验证码页、环境异常页或登录页当成文章正文。
- 不输出或记录 API key、Cookie、Token 等 secret。
- 私人模式可忽略来源许可引用，但不能关闭公网 HTTPS/SSRF、secret、超时/容量、身份和正文质量门禁。
- 需要服务端 Token、登录或付费鉴权的来源在缺少凭据时必须如实失败，不实现技术绕过。
- 不自动执行生产 cron、消息外发或商业 API 消费；只生成配置和命令，除非用户另行明确授权。
