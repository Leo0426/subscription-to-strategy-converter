# AI session routing and inline rule budget

Status: accepted

The Leo AI policy must keep core login, API and content requests on a manually selected egress even before external RuleProviders load. The pinned Claude provider predates `claude.com` and `claudeusercontent.com`; the catalog also omitted those domains. The broader `ai-4` source already contains them, so an ordinary loaded default policy does cover them. However, an explicit Claude override retargeted only the older domain pair and dedicated provider, leaving newer login/content domains on the default AI egress. Without remote providers, the default policy also lacked deterministic core coverage. OpenAI's documented Apple, support, payment and telemetry dependencies likewise lacked inline protection from vendor/ad rules.

## Decision

- Extend the shared service catalog and its generated Leo block with Claude's four service suffixes and OpenAI's documented remaining dependencies. Shared Stripe, Sentry, Datadog and Apple hosts use exact matches; do not route their whole parent domains through AI. Keep the existing Claude RuleProvider for compatibility.
- Route the three exact Google OpenID Connect hosts documented for authorization, token exchange and user information (`accounts.google.com`, `oauth2.googleapis.com`, `openidconnect.googleapis.com`) with the OpenAI service. Keep broad Google domains on the Google policy so ChatGPT sign-in can share its selected egress without capturing unrelated Google traffic.
- AI 服务 offers 美国节点, 新加坡节点 and 手动选择. All are manual selectors; generic latency tests cannot authorize AI egress changes. Empty region pools are pruned, and users can choose a usable node from the remaining pool or the global manual selector. Neither a region label nor a 204 probe proves service acceptance. Use a fixed ServiceRoute when a service needs an independent node.
- Remove five unconditional DIRECT port rules (3478, 5349, 19302, 10000, 5350). Keep named public STUN exceptions. Domainless voice traffic now follows ordinary geographic/final routing; this does not promise that it follows a domain-specific AI override. Do not introduce shared-infrastructure IP rules to guess its service.
- Raise the inline rule budget from 140 to 150 and source size from 12 to 13 KiB. Adding the manually selected Singapore pool raises the group, membership, probe and rendered-output budgets while leaving provider and rule budgets unchanged. Do not delete unrelated service coverage or hide domains in opaque logical expressions merely to fit the old count.
- For Surge iOS, add a curated domestic ByteDance/DouYin domain subset after TikTok and other named overseas services. Surge iOS cannot execute `GEOSITE,cn` or Mac-only `PROCESS-NAME`, while `GEOIP,cn,no-resolve` intentionally does not resolve `.com` hostnames. Keep the upstream `USER-AGENT,TikTok*` and TikTok process entry out, retain the proxied final policy, and do not add the full China list. This raises the source/rule/render budgets to 15 KiB, 185 rules, and 37 KiB without adding a ninth provider.

## Measured cost

Using the existing fixed 144-node SS fixture, the dependency hardening and subsequent amendments measure as follows:

| Measure | Before | Dependency hardening | Singapore pool | Google OAuth alignment |
| --- | ---: | ---: | ---: | ---: |
| Inline template rules (including provider references) | 139 | 146 | 146 | 149 |
| Source bytes | 12,093 | 12,644 | 13,024 | 13,152 |
| Rendered Mihomo bytes | 33,768 | 34,112 | 34,822 | 34,956 |
| RuleProviders / ProxyGroups | 8 / 14 | 8 / 14 | 8 / 15 | 8 / 15 |
| Group member edges | 380 | 378 | 402 | 402 |
| Potential probe memberships | 198 | 198 | 221 | 221 |

All eight provider URLs remain pinned and unchanged; the live audit is regenerated with the template digest. Cold-start rule downloads remain 115,603 bytes with no additional remote fetch; the Singapore selector adds one manual health-check group without enabling automatic failover.

The domestic ByteDance amendment keeps the same eight providers and remote download total. It raises inline rules from 149 to 183, source bytes from 13,152 to 14,718, and the fixed 144-node rendered Mihomo artifact from 34,956 to 36,317 bytes. The added entries are explicit domain rules that Surge iOS can execute; no process, user-agent, IP, or broad China rule was added.

## Verification and limits

Regression tests exercise Profile publication, catalog RulePacks, fixed and legacy Claude routes, and Mihomo/Surge/Shadowrocket output. Inline rules must precede providers and ad filtering. Client-side cached selections and legacy saved PolicySnapshots require an explicit refresh/upgrade. Tests establish emitted routing, not real-node login, regional eligibility or full streaming-session success.

Sources checked through 2026-09-22: [Claude Code network requirements](https://code.claude.com/docs/en/network-config), [OpenAI network recommendations](https://help.openai.com/en/articles/9247338), [Google OpenID Connect API reference](https://developers.google.com/identity/openid-connect/reference), [Surge process rules](https://manual.nssurge.com/rules/process.html), [Surge rule evaluation and DNS semantics](https://manual.nssurge.com/rules/overview.html), [pinned ByteDance rules](https://github.com/blackmatrix7/ios_rule_script/blob/8818705adee20571a856daf11c9fc69c4929109a/rule/Surge/ByteDance/ByteDance_Resolve.list), and [pinned DouYin rules](https://github.com/blackmatrix7/ios_rule_script/blob/8818705adee20571a856daf11c9fc69c4929109a/rule/Surge/DouYin/DouYin.list).
