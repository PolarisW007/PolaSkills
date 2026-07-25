# 来源策略与能力边界

## 结论

不存在可对任意第三方微信公众号同时做到“零登录、纯公网、免费、完整、长期稳定”的官方或开源方案。将发现层做成多源 adapter，并将完整性作为可观测状态。

## 来源矩阵

| 来源 | 登录 | 发现能力 | 完整性 | 默认用途 |
| --- | --- | --- | --- | --- |
| 微信官方发布 API | 目标账号授权 | 自有/授权账号 | 高 | 不用于任意竞品号 |
| 微信 Album | 无 | 合集内文章 | 部分 | 免费主源/校验 |
| 微信 Homepage | 无 | 主页模板或栏目 | 部分 | 免费主源/校验 |
| 官网 RSS/Atom | 无或订阅 Token | 发布方 feed | 视发布方而定 | 增量发现/校验 |
| 发布方 sitemap | 无 | 发布方网站 URL | 部分 | 低频缺口审计 |
| 已知文章 URL | 无或受风控 | 单篇 | 不适用 | 正文读取/身份验证 |
| 搜索引擎 | 无 | 部分索引 | 低、延迟 | 人工补漏 |
| 商业公众号 API | 调用方 API key | 今日/历史文章 | 供应商声明 | 生产 POC 候选 |

标准模式可以用 `permission_required/permission_reference` 管理来源许可。用户明确选择
`private_opt_in` 时，Skill 不再执行许可引用门禁，但继续报告来源身份、失败和 `partial` coverage。

## 免费微信端点

Album：

```text
GET https://mp.weixin.qq.com/mp/appmsgalbum
  ?action=getalbum
  &__biz=<biz>
  &album_id=<album_id>
  &count=20
  &f=json
```

响应通常包含：

- `base_resp.ret`
- `getalbum_resp.article_list`
- `getalbum_resp.continue_flag`
- `getalbum_resp.base_info`

分页使用上一页最后一项的 `msgid` 和 `itemidx`：

```text
&begin_msgid=<msgid>&begin_itemidx=<itemidx>
```

Homepage：

```text
POST https://mp.weixin.qq.com/mp/homepage
  ?__biz=<biz>
  &hid=<hid>
  &begin=0
  &count=5
  &action=appmsg_list
```

响应通常包含 `base_resp.ret`、`appmsg_list` 和 `has_more`。

两个端点都可能改变或被风控。HTTP 200 也必须校验 JSON schema 和 `base_resp.ret`。

## 机器之心案例

### 默认来源：官网 sitemap

机器之心 `robots.txt` 公示：

```text
https://www.jiqizhixin.com/shared/sitemap.xml.gz
```

因此 Skill 默认只以低频方式读取该 gzip sitemap，筛选
`https://www.jiqizhixin.com/articles/` 文章 URL，并用 URL 中显式日期按本轮
`lookback_hours` 筛选。近 30×24 小时查询必须显式使用 `--lookback-hours 720 --backfill`。
具体限制：

- 这是机器之心网站文章流，不是微信公众号 `almosthuman2014` 的完整历史。
- `lastmod` 不默认作为发布时间；只有匹配显式日期正则时才进入时间窗口。
- sitemap 可能包含错误的双 URL 前缀，因此案例显式启用 `nested_https_url`，提取后仍要重新做
  公网 HTTPS 校验。
- CDN 别名当前可能返回 403；使用 `www.jiqizhixin.com` 上 robots 公示的 URL。
- 标准模式的 sitemap 保持 `fetch_content=false`；私人模式可在正文预算内尝试。官网文章可能返回
  HTTP 200 的数据服务提示页而非正文；质量门禁继续识别为
  `publisher_data_service_gate`，不得把它写成文章内容。
- coverage 始终是 `partial`，不能用“近一月无文章”替代“官网 sitemap 未观察到候选”。

目标身份：

```text
wxid: almosthuman2014
biz:  MzA3MzI4MjgzMw==
```

### 私人模式：官方 RSS 与 xInfinite

私人资产同时有效启用官方 RSS 与 xInfinite。官方 RSS 当前服务端仍要求 Token，因此从环境变量向
`query_from_env.token` 注入；缺 Token 时为 `missing_secret`，不能由私人模式绕过。

xInfinite 使用 `entry_link_mode=wechat_original_from_description`。只有 description 包含唯一微信
原文，并校验 `expected_author=机器之心` 和目标 `biz` 后才接受；其它论坛条目被拒绝。该案例把
`max_future_hours` 设为 1，跳过明显的未来时间戳，避免第三方 feed 时钟异常污染增量状态。

官方 RSS 与官方 sitemap 都由同一发布方控制，必须使用相同
`independence_group=machineheart_official`。它们能补充不同展示面，但不是两个独立上游。

微信原文若返回 CAPTCHA，只能保留来源短摘要和链接；不要尝试 Cookie、代理轮换或客户端接口。
私人 sitemap 可以尝试正文，但数据服务提示页仍被 `publisher_data_service_gate` 阻断。

## 第三方来源双策略

标准模式需要单独许可引用时使用：

```json
{
  "type": "rss",
  "url": "https://authorized.example/account.rss",
  "permission_required": true,
  "permission_reference": "non-sensitive-contract-or-approval-id",
  "enabled": true
}
```

`permission_reference` 只记录非敏感线索，不写 Token、Cookie 或个人信息。若
`permission_required=true` 且来源启用但引用为空，标准模式在联网前失败。

私人模式使用：

```json
{
  "defaults": {
    "source_permission_policy": "private_opt_in"
  },
  "targets": [
    {
      "sources": [
        {
          "type": "rss",
          "url": "https://third-party.example/latest.rss",
          "permission_required": true,
          "enable_in_private_mode": true,
          "enabled": false
        }
      ]
    }
  ]
}
```

私人模式只放宽许可引用并打开显式标记来源。需要账号 Cookie、扫码或客户端 Token 的方案仍不接入；
服务端 API/RSS Token 仍必须由用户通过环境变量提供。多个 endpoint 包装同一上游时仍使用同一
independence group。

## 开源项目判断

- WeRSS：调度、RSS、Webhook 和导出能力成熟，但依赖公众号后台扫码/Cookie/Token。
- WeWe RSS：依赖微信读书登录态，且项目已归档。
- wechat-article-exporter：适合导出，但依赖扫码或代理额度。
- RSSHub：适合作为 adapter 包装层；微信部分路由仍可能反爬、需要 Cookie 或只覆盖栏目。
- opencli-weixin-album：证明 Album 列表可无 Cookie 获取；其正文下载依赖浏览器扩展，因此本 Skill 只借鉴公开列表协议。

不要把上述登录态方案包装成“纯公网免费方案”。

## 商业升级

可用 `json_api` adapter 对 Just One API、极致了或企业数据服务做 POC。正式选择前至少比较：

- 20–50 个目标，运行 14–30 天。
- 对人工金标准或多个来源并集的召回率。
- P50/P95 发现延迟。
- 错误公众号归属率。
- 正文成功率、验证码率和删除修正。
- 单篇验证后文章成本。
- 合同 SLA、数据来源和退出/数据可携带性。

关键目标使用两个真正独立的发现源。供应商维护的 APP 内部接口或账号池只是将登录和风控转移到供应商，不等于官方授权。

同一发布方的 RSS、sitemap、网页列表，以及同一第三方 provider 的 RSS/JSON，不能仅因 URL
不同就算作独立双源。

## 身份规则

- 将 `biz` 作为稳定身份。
- 对微信文章 URL 解析 `__biz`、`mid`、`idx`、`sn`。
- 发现来源绑定某 `biz` 时，文章 URL 中不同 `__biz` 必须拒绝。
- URL 没有 `__biz` 时标记 `unverified`，不要静默假定正确。

## 参考

- 微信官方发布记录 API：https://developers.weixin.qq.com/doc/service/api/public/api_freepublish_batchget
- 微信 access token：https://developers.weixin.qq.com/doc/service/api/base/api_getaccesstoken
- 机器之心 robots：https://www.jiqizhixin.com/robots.txt
- 机器之心公开 sitemap：https://www.jiqizhixin.com/shared/sitemap.xml.gz
- 机器之心数据服务与当前权益说明：https://www.jiqizhixin.com/short_urls/13d44681-c065-494d-b657-5ae89df1915a
- xInfinite 服务条款：https://www.xinfinite.net/tos
- Album 公开实现：https://github.com/SlowGrowth1314/opencli-weixin-album
- RSSHub Homepage 路由：https://github.com/DIYgod/RSSHub/blob/master/lib/routes/wechat/mp.ts
- WeRSS：https://github.com/rachelos/we-mp-rss
- WeWe RSS：https://github.com/cooderl/wewe-rss
- Just One API：https://docs.justoneapi.com/zh/api/wechat-official-accounts/
- 公众号数据抓取相关案例：https://www.ciplawyer.cn/articles/147398.html
