# Surge iOS 国内域名兜底

Status: accepted

Surge iOS 会跳过 Leo 的 `GEOSITE,cn,DIRECT`，而 `GEOIP,cn,DIRECT,no-resolve` 不会为尚未解析的域名主动查 IP；因此 ADR 0015 中仅内联国内字节系域名的范围，仍使许多国内 `.com` 域名落入代理兜底。Leo 增加一个固定提交的 China RuleProvider，排在具名服务规则之后、`FINAL` 之前。Mihomo 使用该来源的 classical 版本，Surge iOS 使用同一提交的纯域名 `DOMAIN-SET`，避免上游完整 Surge 列表中 `USER-AGENT,Microsoft*` 等宽泛规则覆盖明确的服务分流。此决策修订 ADR 0015 的「不新增第九个来源、不加入 China 列表」约束；公开 RuleSource 审计、来源数量与冷启动预算必须随之更新。

该国内域名集无法涵盖每一个国内域名，也不负责覆盖其余客户端的 DNS、网络或节点行为。遇到仍被代理的请求，应以 Surge iOS「最近请求」里的实际域名和命中规则定位下一处缺口。
