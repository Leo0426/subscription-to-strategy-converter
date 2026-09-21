# AI session routing and inline rule budget

Status: accepted

The Leo AI policy must keep core login, API and content requests on a manually selected egress even before external RuleProviders load. The pinned Claude provider predates `claude.com` and `claudeusercontent.com`; the catalog also omitted those domains. The broader `ai-4` source already contains them, so an ordinary loaded default policy does cover them. However, an explicit Claude override retargeted only the older domain pair and dedicated provider, leaving newer login/content domains on the default AI egress. Without remote providers, the default policy also lacked deterministic core coverage. OpenAI's documented Apple, support, payment and telemetry dependencies likewise lacked inline protection from vendor/ad rules.

## Decision

- Extend the shared service catalog and its generated Leo block with Claude's four service suffixes and OpenAI's documented remaining dependencies. Shared Stripe, Sentry, Datadog and Apple hosts use exact matches; do not route their whole parent domains through AI. Keep the existing Claude RuleProvider for compatibility.
- AI 服务 offers 美国节点 and 手动选择 only. Both are manual selectors; generic latency tests cannot authorize AI egress changes. Without US-labelled nodes, users must choose a usable node manually. Neither a region label nor a 204 probe proves service acceptance. Use a fixed ServiceRoute when a service needs an independent node.
- Remove five unconditional DIRECT port rules (3478, 5349, 19302, 10000, 5350). Keep named public STUN exceptions. Domainless voice traffic now follows ordinary geographic/final routing; this does not promise that it follows a domain-specific AI override. Do not introduce shared-infrastructure IP rules to guess its service.
- Raise the inline rule budget from 140 to 150 and source size from 12 to 13 KiB. Provider, group, probe and rendered-output budgets remain unchanged. Do not delete unrelated service coverage or hide domains in opaque logical expressions merely to fit the old count.

## Measured cost

Using the existing fixed 144-node SS fixture, before → after:

| Measure | Before | After |
| --- | ---: | ---: |
| Inline template rules (including provider references) | 139 | 146 |
| Source bytes | 12,093 | 12,644 |
| Rendered Mihomo bytes | 33,768 | 34,112 |
| RuleProviders / ProxyGroups | 8 / 14 | 8 / 14 |
| Group member edges | 380 | 378 |
| Potential probe memberships | 198 | 198 |

All eight provider URLs remain pinned and unchanged; the live audit is regenerated with the template digest. Cold-start rule downloads remain 115,603 bytes, with no additional remote fetch or health-check group.

## Verification and limits

Regression tests exercise Profile publication, catalog RulePacks, fixed and legacy Claude routes, and Mihomo/Surge/Shadowrocket output. Inline rules must precede providers and ad filtering. Client-side cached selections and legacy saved PolicySnapshots require an explicit refresh/upgrade. Tests establish emitted routing, not real-node login, regional eligibility or full streaming-session success.

Sources checked 2026-09-21: [Claude Code network requirements](https://code.claude.com/docs/en/network-config), [OpenAI network recommendations](https://help.openai.com/en/articles/9247338).
