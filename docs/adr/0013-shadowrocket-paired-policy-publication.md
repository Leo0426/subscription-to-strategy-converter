# Shadowrocket uses paired node and policy publications

Status: accepted

The operator requires Clash, Surge, and Shadowrocket as public client families. This expands the two-target boundary recorded in ADR 0006 and CONTEXT while retaining the same Profile and PolicySnapshot lifecycle.

Shadowrocket receives a Clash-compatible node subscription (`target=shadowrocket`) and an accompanying native policy configuration (`target=shadowrocket-config`). The UI exposes both import steps. Modern node fields remain in the structured node representation; the native configuration owns service groups and ordered compatible Leo rules. This follows the node format used by [Sub-Store's Shadowrocket producer](https://github.com/sub-store-org/Sub-Store/blob/master/backend/src/core/proxy-utils/producers/shadowrocket.js) and the group/rule syntax in the [authored Shadowrocket configuration examples](https://github.com/LOWERTOP/Shadowrocket/blob/main/lazy_group.conf), inspected on 2026-09-08.

## Trade-off

A single native INI file would simplify import but require maintaining a second protocol/transport serializer with incomplete mappings for evolving protocols. A node-only subscription would omit the product's policy intent. Paired outputs preserve both capabilities at the cost of two imports and updating both resources when nodes change.

## Consequences

- `subscribe_urls` contains the three client families; `config_urls.shadowrocket` supplies the companion policy URL. Both Shadowrocket resources use the same token and Profile intent, with distinct cached artifacts and no additional database lifecycle.
- The two resources are generated on request, not as an atomic upstream snapshot. Users update both when node names change.
- Surge and Shadowrocket share INI assembly and group-reference closure; client-specific rules, probes, and node representation stay in their respective compilers.
- Unsupported output protocols and rules are reported. Removed node/group targets reject traffic instead of silently changing to DIRECT; an entirely unsupported Shadowrocket node inventory fails generation.
- Clash support means Mihomo/Clash.Meta-compatible clients; legacy Clash cores and arbitrary old client versions are not promised full Leo compatibility. Client runtime import/connectivity still needs device QA.
