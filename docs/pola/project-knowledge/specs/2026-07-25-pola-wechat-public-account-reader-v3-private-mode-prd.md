# PRD：pola-wechat-public-account-reader V3 私人宽松模式

artifact: product-spec

## 产品目标

让个人用户用一个明确开关打开预先标记的 RSS、第三方来源和正文尝试，不再要求填写来源许可引用；
同时让报告清楚显示“本轮使用了私人策略”，并保持内容真实性和系统安全。

## 模式定义

| 模式 | 配置值 | 行为 |
| --- | --- | --- |
| 标准模式 | `enforced` | 与 V2 相同；启用且要求许可的来源缺引用时校验失败 |
| 私人模式 | `private_opt_in` | 放宽空 `permission_reference`；启用明确标记的私人来源 |

用户可在配置 `defaults.source_permission_policy` 中持久选择，也可对 `validate`、`run`、`cron` 使用
`--private-use` 做本次覆盖。

## 主流程

1. 用户复制 `assets/targets.machineheart.private.json`，或在已有来源设置
   `enable_in_private_mode=true`。
2. 用户运行 `validate --private-use`。
3. 命令显示最终策略、三个有效来源以及被私人模式放开的来源。
4. 用户执行 `run --private-use --dry-run`。
5. runtime 尝试 sitemap、官方 RSS 和 xInfinite：
   - 没有 RSS Token 时只记录 `missing_secret`。
   - xInfinite 条目只有通过作者、唯一微信原文和 `biz` 校验才进入候选。
   - sitemap 正文尝试仍受预算；数据服务页不进入摘要。
6. 报告输出新增、失败、coverage、策略和 override；用户确认后再正式建立 checkpoint。

## 状态和异常

- 私人模式不是“忽略所有 enabled”：停用 target 和未标记的停用来源保持停用。
- 私人模式不是“忽略鉴权”：需要 Token 的服务没有 Token 时仍失败。
- 单个私人来源失败不取消同 target 或其它 target 的健康来源。
- 三来源健康也只能提高可观测性，不能自动把 coverage 标成 `complete`。
- 来源返回维护页、登录页、验证码或数据服务提示页时，必须标记 `blocked`。

## 配置与报告

新增配置字段：

```json
{
  "defaults": {
    "source_permission_policy": "private_opt_in"
  },
  "targets": [
    {
      "sources": [
        {
          "enabled": false,
          "enable_in_private_mode": true,
          "fetch_content": false,
          "fetch_content_in_private_mode": true,
          "permission_required": true
        }
      ]
    }
  ]
}
```

报告 `selection` 新增：

- `source_permission_policy`
- `private_mode_enabled_sources`
- `private_mode_content_sources`
- `permission_overrides`

所有列表只保存 `target_id/source_key`，不保存 Token、Cookie 或环境变量值。

## 非功能要求

- 旧 `version=1` 配置无需迁移。
- 默认 `enforced`，避免旧任务静默扩大访问面。
- 所有新增布尔字段严格接受 JSON `true/false`。
- CLI 的私人覆盖必须进入 cron 生成结果，保证定时行为可复现。
- 无界重试、并发、响应、gzip、候选、正文或日志增长均不允许。

## 验收标准

复用 Requirement 的 A1–A12；其中 A3、A4、A7、A8、A10、A11 必须有自动化测试，A6 需要显式
公网 smoke 或记录外部退化。
