# Subflow Strategy Builder — Domain Context

## Mission

A self-hosted Clash/Mihomo, Surge and Shadowrocket policy release control plane built around one consolidated `leo.yaml` template.

## Product Boundary

- Subflow owns policy intent, structural transformation, semantic validation, target-specific releases, and subscription lifecycle.
- Protocol parsing and broad format conversion are compatibility inputs, not the product's differentiating capability.
- Airport subscriptions own connectivity and common settings for all three public clients; Subflow owns routing rules and the policy groups needed to express them. Same-format publication preserves the source envelope; cross-format publication maps equivalent settings and reports compatibility limits (ADR 0016).
- Clash/Mihomo is the semantic quality target; Surge and Shadowrocket are public compatibility targets with explicit warnings for skipped protocols and rule sets.
- Business policy is assembled from visible RulePacks; RouteIntent and reusable NodePools optionally override the selected packs' egress behavior.
- The initial operator is one advanced user running a private local or self-hosted deployment; public conversion SaaS and multi-tenancy are outside the current scope.

## 10 Pain Points (Design Compass)

| # | Problem | Domain signal |
|---|---------|---------------|
| 1 | **YAML 地狱** | configs not maintainable as raw text |
| 2 | **规则碎片化** | rules copied from GitHub without cohesion |
| 3 | **平台不兼容** | Mihomo / Surge / sing-box need hand-crafted formats |
| 4 | **规则不可视化** | no DAG or graph to see how rules chain |
| 5 | **缺少依赖管理** | rule-providers have no version pinning |
| 6 | **缺少版本控制** | hard to roll back a config change |
| 7 | **缺少规则分析** | conflicts, overlaps, dead rules go undetected |
| 8 | **缺少统一抽象** | everything is raw text, no IR or type system |
| 9 | **无法多人协作** | no shared workspace, no diff, no review |
| 10 | **无运行时观测** | no tracing; can't tell which rule matched |

New issues should state which pain points they address.

## Domain Vocabulary

| Term | Meaning |
|------|---------|
| **ProxyNode** | A single proxy server entry (SS, Trojan, VMess, etc.) — the canonical IR type |
| **NodeSelector** | A stable, named query over the current `ProxyNode` inventory using include/exclude name regexes and optional protocols; referenced as `selector:<id>` by proxy groups |
| **NodePool** | A product-facing, reusable set of nodes declared by region, protocol, include keywords, and exclude keywords; compiled into a NodeSelector |
| **RouteIntent** | A product-facing declaration containing NodePools and per-service primary pool, optional fallback pool, and final target |
| **Leo Template** | The single supported policy source at `community_templates/leo/leo.yaml`; it supplies proxy groups, providers and the full ordered rule graph. Its standalone DNS/TUN defaults do not override subscription connectivity. |
| **PolicyPreset** | A small product-facing, named starting policy graph that is copied into a Profile and may then be freely composed |
| **RulePack** | A selectable product module containing one business target group, its dependent groups, and the concrete ordered rules that route to it |
| **RulePackSelection** | The ordered set of RulePack identifiers chosen by a user and compiled into a PolicySnapshot |
| **PolicySnapshot** | The complete `SelectedPolicy` stored in a Profile after preset selection or custom composition; later preset changes do not mutate it |
| **PolicyWorkspace** | Product core for the MVP: an in-memory policy workspace holding nodes, groups, rules, providers, settings, graph data, analyzer findings, simulator traces, and compile output |
| **PolicyWorkbench** | The single-page product surface for source connection, fine-grained ServiceRoute overrides, validation, Profile publication, and public Leo audit evidence |
| **Profile** | A mutable policy intent containing one authorized source, ServiceRoutes, and target-specific publication choices; its generation identifies the current saved edit, without retaining edit history |
| **SurgePreferences** | Explicit Profile intent for Surge-only publication behavior; `auto_test_protocols` restricts node members of automatic test groups without removing nodes from manual selectors |
| **ServiceRoute** | One entry in a RouteIntent that maps a catalog service to a primary NodePool, optional fallback NodePool, and final target |
| **RuleSource** | A policy-rule input identified by its origin, format, version, and content digest |
| **ProxyGroup** | A named group of nodes or groups with a dispatch strategy (select / url-test / fallback / load-balance) |
| **RuleProvider** | An external rule-set URL referenced by name in rules (Clash: `rule-providers`) |
| **TemplatePolicyTransform** | A structure-aware operation that preserves a selected template and changes only a recognized service-policy subgraph |
| **Claude Egress** | The explicit node or policy group placed first in the template's dedicated Claude policy group |
| **Legacy Template** | A community or historical full YAML skeleton retained as an import and API compatibility source, not a primary product choice |
| **Compiler** | A platform-specific module (`surge.py`, `singbox.py`) that takes (nodes, groups, rules, providers) → formatted config string |
| **Subscription URL** | The stable `/subscribe?...` endpoint URL users paste into their proxy client |
| **MATCH / FINAL** | Catch-all rule — Clash calls it `MATCH`, Surge calls it `FINAL` |
| **MRS** | Mihomo binary rule-set format; must be substituted with `.txt` URLs for Surge |

## Architecture Layers

```
Authorized Subscription
    ↓ protocol compatibility boundary
ProxyNode inventory
    ↓ Leo Template + optional RulePackSelection
PolicySnapshot
    ↓ optional RouteIntent egress overrides
PolicyWorkspace
    ↓ analyze + simulate + target validation
Clash/Mihomo artifact + Surge artifact + Shadowrocket nodes and policy
    ↓ persisted as the Profile's last-successful artifact (ADR 0002)
Token-protected Subscription URLs
```

## Module Map

| Module | Role |
|--------|------|
| `app/ir.py` | All IR dataclasses: `ProxyNode`, `ProxyGroup`, `PolicyRule`, `RuleProvider`, `PolicyWorkspace`, graph/analysis/simulation types |
| `app/core/parser.py` | Raw Clash YAML parsing |
| `app/core/parsers/clash.py` | `clash_to_ir()` and `ir_to_clash_dict()` — bridge between Clash dicts and `ProxyNode` |
| `app/core/parsers/surge.py` | Parses supported Surge `[Proxy]` entries into `ProxyNode` while rejecting malformed recognized entries |
| `app/core/parsers/shadowrocket.py` | Reads native URI/Base64/INI policy inventory and retains the complete native profile; opaque nodes must never be serialized as converted connections |
| `app/core/normalizer.py` | Post-parse dedup and normalization for `ProxyNode` lists |
| `app/core/fetcher.py` | HTTP fetching with SSRF safety checks |
| `app/core/subscription.py` | `load_subscription()` — end-to-end: URL → Clash YAML or Surge config → normalized `ProxyNode` list |
| `app/core/subconverter.py` | Optional compatibility Adapter: unsupported subscription URL → node-only Clash YAML through an operator-configured Subconverter |
| `app/core/template_engine.py` | Built-in preset definitions, local template loader, `apply_template()`, `list_templates()`, and target-local automatic-test protocol filtering |
| `app/core/powerfullz.py` | Fetches powerfullz static YAML from jsDelivr CDN |
| `app/core/policy_workspace.py` | Workspace conversion boundary: `config_to_workspace()`, `workspace_from_dict()`, `workspace_to_mihomo_config()`, `compile_mihomo_config()` |
| `app/core/policy_graph.py` | `build_policy_graph()` → `PolicyGraph` (nodes + edges) |
| `app/core/policy_analyzer.py` | `analyze_workspace()` → `list[AnalyzerFinding]` |
| `app/core/policy_simulator.py` | `simulate_destination()` → `SimulationTrace` |
| `app/core/policy_catalog.py` | Extracts and deduplicates policy entries across community templates |
| `app/core/rule_packs.py` | Exposes concrete business rule cards and assembles a RulePackSelection into a PolicySnapshot |
| `app/core/policy_resolution.py` | Applies the PolicyPreset/RulePackSelection/RouteIntent precedence to a ConvertRequest; raises `PolicyResolutionError` |
| `app/core/intent_compiler.py` | Compiles product-facing NodePools and ServiceRoutes into NodeSelectors, ProxyGroups, and ordered rules |
| `app/core/template_policy_transform.py` | ServiceRoute transformation boundary with Claude template analysis and compatibility adapters |
| `app/core/profiles.py` | Persistent Profile store with token authorization and last-successful artifact caching |
| `app/core/provider_egress.py` | Decides which RuleProviders must download through a ProxyGroup instead of the direct route |
| `app/core/rule_source_audit.py` | RuleSource availability/content/supply-chain audit, structural-v2 quality score, and the published `audit.json` snapshot |
| `app/core/renderer.py` | `render_yaml()` — serializes a dict to YAML string |
| `app/core/platforms/surge.py` | Public Surge compatibility compiler; reports skipped protocols and unsupported MRS rule sets |
| `app/core/platforms/surge_capabilities.py` | Shared Surge iOS rule capability set used by template analysis and compilation |
| `app/core/platforms/surge_audit.py` | Redacted, advisory audit of native Surge settings and emitted TLS verification risk |
| `app/core/platforms/surge_profile.py` | Replaces native Surge routing while preserving the provider's connectivity sections and auxiliary groups |
| `app/core/platforms/mihomo.py` | Merges compiled policy into the native source envelope, retaining connectivity and disambiguating generated references |
| `app/core/platforms/mihomo_dependencies.py` | Removes unreachable native policy definitions while retaining the transitive dependencies of connectivity and generated policy |
| `app/core/platforms/shadowrocket.py` | Public Shadowrocket node subscription and companion native policy compiler |
| `app/core/platforms/ini.py` | Shared INI artifact assembly, compatibility warnings and target-group closure |
| `app/core/platforms/singbox.py` | Experimental sing-box compiler |
| `app/core/sessions.py` | In-memory session store for large policy payloads (avoids huge query strings in `/subscribe`) |
| `app/core/config_tree.py` | Preview tree builder for raw Clash config |
| `app/api/convert.py` | Main API router — workspace, convert, subscribe, simulate, compile endpoints |
| `app/api/community.py` | Community template catalog API |
| `app/api/health.py` | Health check |
| `app/api/system.py` | Lightweight application and Profile database status API |
| `app/models/` | Pydantic request/response models |
| `app/models/surge.py` | Persisted `SurgePreferences` model and protocol normalization |

## Template Boundary

The only public template is `local:community_templates/leo/leo.yaml`. Historical presets remain code-defined in `app/core/template_engine.py::PRESET_TEMPLATES` only as internal compatibility and RulePack source material; template-backed endpoints never expose or accept them:

| ID | Description |
|----|-------------|
| `minimal` | Core groups only: Proxy / Auto / Fallback / DIRECT |
| `developer` | GitHub, npm, Docker, JetBrains, Microsoft, Apple splits |
| `ai-tools` | Claude, OpenAI, Gemini, Perplexity, Cursor, GitHub Copilot splits |
| `streaming` | Netflix, YouTube, Disney, Spotify, Telegram splits |
| `full` | AI + Developer + Streaming + geo groups (HK / SG / JP / US) |
| `powerfullz` | powerfullz/override-rules static YAML, fetched from jsDelivr at request time |

The community catalog, policy catalog, page and conversion/Profile interfaces are all pinned to `community_templates/leo/leo.yaml`.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/system/status` | Application and Profile database status used by the page health indicator |
| GET | `/templates` | Return the single supported Leo template |
| GET | `/templates/detail` | Leo template structure and YAML preview; other template IDs are rejected |
| GET | `/templates/source` | Return the complete public Leo YAML source |
| GET | `/templates/audit` | Return the versioned RuleSource audit snapshot and publication metadata |
| GET | `/community/rules` | Return every top-level rule and RuleProvider source from Leo |
| GET | `/policy-catalog` | Extracted rule providers across community templates |
| GET | `/rule-packs` | List selectable RulePacks, concrete rules, dependencies, categories, and preset defaults |
| GET | `/intent/catalog` | List supported service and region choices for RouteIntent editors |
| POST | `/preview` | Parse subscription → node list + config tree |
| POST | `/convert` | Full conversion → rendered config string |
| POST | `/workspace/preview` | Build workspace + graph + analyzer findings |
| POST | `/render` | Render one target from a structured ConvertRequest body without query-string size limits |
| GET | `/claude/templates` | List templates containing Claude policy with target-specific compatibility metadata |
| POST | `/analyze` | Re-analyze an existing workspace dict |
| POST | `/simulate` | Simulate a destination through workspace rules |
| POST | `/compile/mihomo` | Compile workspace dict → Mihomo YAML |
| POST | `/session` | Store large policy payload, return session ID |
| POST | `/profiles` | Persist one Leo-based Profile and return token-protected Clash, Surge and Shadowrocket Subscription URLs plus the Shadowrocket policy URL |
| GET | `/profiles` | List redacted local Profile summaries without token or source subscription URL |
| GET | `/profiles/{profile_id}/draft` | Read an editable Profile conversion intent with token authorization |
| PUT | `/profiles/{profile_id}` | Replace a Profile conversion intent with token authorization and invalidate old artifacts |
| GET | `/subscribe/{profile_id}` | Compile a persisted Profile for `target=clash|mihomo|surge|shadowrocket|shadowrocket-config` or return its target-specific stale artifact |
| GET | `/subscribe` | Stable URL for proxy clients — returns config directly |

## Platform Support

| Platform | Priority | Compiler |
|----------|----------|---------|
| Mihomo / Clash | Product semantic quality bar | `app/core/policy_workspace.py` → `workspace_to_mihomo_config()` + `app/core/renderer.py` |
| Surge 5.21+ | Public compatibility target; unsupported protocols and MRS sources are skipped with warnings | `app/core/platforms/surge.py` |
| Shadowrocket | Original native subscription plus policy overlay / native config with replaced routing | `app/core/platforms/shadowrocket.py` |
| sing-box | Internal experimental compiler; rejected by Leo-backed product interfaces | `app/core/platforms/singbox.py` |

## Key Invariants

- `ProxyNode` is the representation used by policy logic. Native source envelopes are carried separately to platform compilers for lossless publication; policy transformations do not mutate them.
- Native Surge output preserves all non-routing sections, raw proxy parameters, original node identifiers and aliases; normalized inventory references map back to native names. Source groups remain for native dependencies, while colliding generated groups receive unique Subflow-prefixed names. Ambiguous node/group identifiers fail clearly. Only the original managed-update directive is removed so refreshes cannot revert to the raw upstream rules. Cross-format output still uses the compatibility compiler. A native-origin policy-only Workspace cannot be compiled to Surge without the source context; callers use the render/subscription path instead.
- `surge_preferences.auto_test_protocols` is explicit Profile intent applied only while building Surge output. It filters node members of `url-test` groups after template materialization; nested groups remain, manual selectors retain every compatible node, and an absent or empty preference preserves legacy membership.
- The public `surge` target means Surge iOS. The analyzer and compiler share one capability set; Mac-only `PROCESS-NAME` rules are skipped with both unique-type and skipped-rule counts. Native `[General]` remains source-owned: audits are advisory and redacted, and `skip-cert-verify` is never automatically disabled.
- Clash fields not yet modeled by `ProxyNode` must survive Mihomo round trips through the private `_clash_passthrough` payload; policy code must not depend on that payload
- AnyTLS models implicit TLS, password and reuse in ProxyNode while retaining other Mihomo fields. Surge import/export maps compatible TLS options and reports minimum client versions; non-equivalent tuning is warned, while unmapped node/security options exclude only the affected node. Fixed ServiceRoutes cannot publish an excluded node even when peers use the same protocol. See `docs/anytls-compatibility.md`.
- Native Clash/Mihomo publication preserves all source common settings, including DNS, Hosts, ports and TUN; absent source settings stay absent. Keep raw nodes and only source groups/providers/sub-rules reachable from retained connectivity or generated policy, including transitive dependencies and dynamic provider inclusion. Replaced source rules never keep obsolete definitions alive. Prune before resolving generated group/provider names and cache paths; remaining collisions are renamed with their references, and ambiguous node identities fail clearly. The only general override is rule mode when required for routing, with a warning.
- Mihomo Workspace preview materializes the effective source envelope once; same-target compilation retains finalized native provider egress and compile warnings. Lossy native-node IR round trips and cross-format previews missing source context direct callers to render/subscription instead of silently losing settings. Provenance markers stay outside client settings.
- Reachable HTTP/file or expression-driven ProxyProviders can contain hidden dialer/rematch dependencies. Preserve their possible native group/sub-rule dependencies and report this limitation; unreachable external providers remain eligible for pruning. Do not add provider downloads to policy compilation merely to infer reachability.
- Shadowrocket requests its own native response, preserves the subscription text exactly, and keeps all non-routing sections when a complete native INI is available. A nodes-only source yields a policy-only companion with no General/Host defaults. Never rebuild Shadowrocket common settings from Mihomo fields. Source node names map back from normalized inventory references; native nodes are not filtered by a cross-format serializer's protocol list. Publication identity remains in HTTP headers instead of altering the native body.
- Surge compilation reports `unsupported_node_dns` when emitted domain-backed nodes need node-only resolver settings that it cannot preserve; diagnostics must never expose resolver URLs or subscriber tokens, and must not silently widen these settings to all traffic-domain DNS
- Subconverter is an opt-in input compatibility Adapter used only after direct Clash/Surge parsing fails; it never owns templates, rules, target rendering, or the Profile lifecycle
- Universal subscriptions use target-specific negotiation: Mihomo/Clash.Meta by default, Shadowrocket for its native subscription and companion config. The global `SUBFLOW_SUBSCRIPTION_USER_AGENT` override is subordinate to `SUBFLOW_SHADOWROCKET_USER_AGENT` for Shadowrocket; both affect publication identity. Native Shadowrocket Base64/URI inputs require no adapter. Other cross-format Base64/URI inputs retain the opt-in Adapter boundary.
- Every URL forwarded to an external fetcher (subscription source, Subconverter) must pass the same DNS-rebinding check as `fetch_subscription()`; a hostname that resolves to a private/loopback IP is rejected before the request is made
- Shadowsocks transport options required for connectivity, including Surge `obfs` and `obfs-host`, must survive input normalization and map to the equivalent target-client syntax
- Cross-format Mihomo HTTP simple-obfs output removes a hostname's trailing empty-port colon (`host:`): Mihomo appends the node port, producing a rejected `host::port` header otherwise. Native Mihomo node fields remain unchanged. This conversion-only normalization must not mutate shared ProxyNode data or alter other plugins, TLS obfs, explicit ports, or IPv6 literals.
- Mihomo output from `/convert` and `/subscribe` must compile through `PolicyWorkspace` via `compile_mihomo_config()`
- Mihomo health probes use HTTP `HEAD`; every probe URL and `expected-status` pair must be validated with `HEAD`. AI traffic uses a manual US-only Selector whose Cloudflare 204 health check updates connectivity and latency but never authorizes automatic node switching; absent US nodes, AI delegates only to manual selection, never a global latency group (ADR 0015)
- ServiceRoute fallback requires two explicit node names. Its generic connectivity probe may switch only between those nodes and never proves that an AI service accepts either exit.
- Mihomo is the first quality-bar compiler; other compilers remain experimental until semantic parity is explicit
- Experimental compilers should report unsupported protocols without breaking the workspace loop
- `RULE-SET` in Surge uses a direct URL (not provider name); the compiler resolves the name via `rule_providers` dict
- Surge 5.21+ is the compatibility baseline; audited blackmatrix7 Classical providers use complete `_All` variants, while unknown Classical mappings fail closed instead of guessing a partial or nonexistent list
- Every `RULE-SET` reference, including references nested inside logical `AND` / `OR` / `NOT` rules, must resolve to a declared RuleProvider before publication
- Surge does not accept Mihomo-only rule types such as `DOMAIN-REGEX`, `PROCESS-NAME-REGEX`, and `IN-NAME`; the compatibility compiler must skip and report them rather than emit an invalid `.conf` line
- Community templates live under `community_templates/` (scanned root); the deduplicated community template is `community_templates/leo/leo.yaml`
- All template IDs from the community are prefixed `local:` (e.g. `local:community_templates/leo/leo.yaml`)
- Sessions in `app/core/sessions.py` are in-memory only; they do not persist across restarts
- Profiles persist in SQLite; access requires both the profile ID and an independent token whose hash is stored in the database
- A Profile has one source subscription, service preferences (or a legacy PolicySnapshot), and target-specific Clash/Mihomo, Surge and paired Shadowrocket publications
- Shadowrocket node subscriptions and companion policy configs have distinct artifact keys. Both share the same Profile/token; users update both resources to keep node names and policy-group references synchronized (ADR 0013).
- Fresh Profile artifacts may be reused within a bounded TTL only for the same saved generation, target and current policy/compiler identity; every request still authenticates. A force refresh bypasses freshness reuse, not shared in-flight work. See `docs/subscription-refresh.md`.
- A Profile may serve a stale artifact only for an external source dependency failure, with the same saved generation and target, and must mark it with `X-Subflow-Stale: true`. An older policy/compiler identity is allowed in this failure case only, retaining its true identity and generation time; malformed source or compile failures cannot fall back.
- Updating a Profile invalidates all previously compiled artifacts before the new intent can be served; an older in-flight request cannot write artifacts into the new generation
- Legacy Claude transforms preserve provider URLs, rule order, DNS/TUN settings, and every non-Claude policy edge. Modern service transforms prepend catalog rules and retarget only that service's owned provider/GEOSITE references while preserving DNS/TUN and unrelated routes (ADR 0014).
- Legacy Claude customization requires a recognizable Claude rule/provider in the selected template. Modern ServiceRoute modes use the shared service catalog (ADR 0014).
- Surge direct service transforms fail closed when they require incompatible template semantics; normal Profile compilation is best-effort and reports skipped protocols and MRS rule sets through warnings
- Protocol and client breadth must not bypass `PolicyWorkspace` or duplicate a mature conversion engine without a demonstrated semantic requirement
- New service-specific routing capabilities extend `ServiceRoute`; they must not introduce a parallel Profile or publishing lifecycle
- `SelectedPolicy.mode=merge` is additive for legacy callers; the structured composer uses `replace` to own proxy groups, rule providers, and ordered rules as one validated policy graph
- New product Profiles and public conversion interfaces use exactly `local:community_templates/leo/leo.yaml`; other template IDs fail validation
- Modern workbench Profiles save ServiceRoute preferences, not copied rule graphs. RulePackSelection and PolicyPreset remain legacy API composition boundaries (ADR 0014).
- RouteIntent is an optional egress override for selected RulePacks; its NodePools compile into NodeSelectors and its ServiceRoutes replace the corresponding target-group members
- Expert composition replaces the complete PolicySnapshot and does not combine implicitly with RouteIntent changes
- PolicyWorkbench keeps creation, stable-link editing, client validation, explicit legacy upgrades and optional service diagnosis on one page and exposes only fine-grained ServiceRoute overrides; template structure and RuleSource evidence remain queryable through the public ledger
- A stored PolicySnapshot does not automatically merge later PolicyPreset changes; updating from a preset is an explicit reset operation
- `NodeSelector` references are expanded against the newly fetched upstream `ProxyNode` inventory on compilation; Profile subscription requests may reuse a fresh artifact within the bounded cache window. Unknown selectors fail closed and selectors producing an empty group are publish-blocking errors
- Rules after the first `MATCH` or `FINAL` are unreachable and must be reported by the analyzer
- Leo's named service GEOSITEs and domestic game exceptions precede broad `gfw` / `geolocation-!cn` routes; generic default-proxy port or inbound-name rules must not preempt China/private direct catchalls. Unclassified traffic uses the final `MATCH`.
- A structurally valid artifact is not necessarily a runnable one; the analyzer reports target-client runtime feasibility (RuleProvider reachability, cold-start provider budget, core version requirements) as warnings that never block publication
- New RuleSources must pass the admission checklist in `community_templates/leo/README.md` (trusted upstream, no third-party proxy fronts, pin when possible, cost-proportional, no high overlap, no target conflicts); the structural-v2 score and the analyzer share one provider-count budget
- Leo is intentionally a lightweight runtime policy: its regression budget is at most 8 RuleProviders, 185 rules, 15 KiB of source YAML, 15 ProxyGroups, 4 health-check groups, 405 total group-member edges, 225 potential probe memberships, and 37 KiB of rendered YAML for the fixed 144-node SS fixture; exceeding one budget requires an explicit architecture decision and cold-start evidence
- IP-layer RULE-SETs may route to a service group only for services with genuine domainless direct-IP traffic (Telegram, Discord voice) and must carry `no-resolve`; shared-infrastructure services (AI, Google, streaming) get no IP-layer routing at all, because their front IPs carry unrelated services and a resolving IP rule splits one page across two egresses — geo/private fallbacks targeting DIRECT legitimately resolve
- Known debt against that boundary: the pinned Google and YouTube classical sources still contain 5 and 3 IP entries. Both source entries and outer references are `no-resolve`, and the public audit exposes their types and resolving count; this containment is not compliance and must not be used as precedent for a new RuleSource
- RuleProviders hosted where the client has no direct route must declare `proxy: <group>`; `provider_egress.py` owns that decision for both the compiler and the analyzer
- The published Subscription URL host is unknowable from the request; the page guesses `location.origin` and `SUBFLOW_PUBLIC_BASE_URL` overrides it for clients running on another host
- There is no Release/ProfileRevision history or rollback; a Profile keeps only its current intent and last-successful artifact per target (ADR 0012)

## Subflow 5 workbench boundaries

- `community_templates/leo/services.json` owns service labels, domain rules, dedicated provider/GEOSITE references and allowed probe URLs; `app/core/service_catalog.py` serves it and `scripts/sync-service-rules.py --check` detects standalone Leo drift.
- `ServiceRoute.mode` is `fixed`, `manual`, `fallback`, or legacy. No route means follow Leo. Fixed means exactly one node; fallback requires two explicit nodes and generic connectivity health checks, which cannot establish service acceptance.
- `publication_targets` opts new workbench writes into compile checks for every selected client. Hard failures block creation/update. Legacy API writes keep their previous validation behavior.
- `/services`, `/check`, `/diagnose`, `/runtime/capabilities`, and token-protected `/profiles/{id}/upgrade-preview` support the single page. Upgrade preview makes no database mutation; PUT keeps ID/token/URLs stable.
- Modern preferences consume current Leo/catalog on each compilation; `policy_revision` identifies the base/catalog at last save, not a full historical revision. Legacy snapshots need explicit upgrade to adopt current service rules.
- Runtime adapters in `app/core/runtime_diagnostics.py` only use operator-configured controller/CLI locations, never browser-provided endpoints or credentials. Mihomo reads group selection (not observed domain matching); Surge reads a live rule explanation. Both probe an observed node without changing client selections. No success claim extends to full browser login/chat.

## ADRs

Current (accepted, authoritative for their area):

- [ADR 0002: Persistent profiles and stale fallback](docs/adr/0002-persistent-profiles-and-stale-fallback.md)
- [ADR 0004: Template-driven Claude policy transforms](docs/adr/0004-template-driven-claude-policy-transforms.md)
- [ADR 0006: Policy release control plane over protocol conversion](docs/adr/0006-policy-release-control-plane-over-protocol-conversion.md)
- [ADR 0007: One canonical base with composable policy presets](docs/adr/0007-one-canonical-base-with-composable-policy-presets.md)
- [ADR 0009: Rule packs as the default assembly boundary](docs/adr/0009-rule-packs-as-default-assembly-boundary.md)
- [ADR 0010: Single-page policy workbench](docs/adr/0010-single-page-policy-workbench.md)
- [ADR 0011: Service-level rule source consolidation](docs/adr/0011-service-level-rule-source-consolidation.md)
- [ADR 0012: No Release/ProfileRevision rollback history](docs/adr/0012-no-release-rollback-history.md)
- [ADR 0013: Shadowrocket paired policy publication](docs/adr/0013-shadowrocket-paired-policy-publication.md)
- [ADR 0014: Service intent workbench and explicit client validation](docs/adr/0014-intent-workbench-and-client-validation.md) — supersedes ADR 0009's default snapshot assembly; legacy boundaries remain
- [ADR 0015: AI session routing and inline rule budget](docs/adr/0015-ai-session-routing-and-inline-budget.md)

Superseded (kept only as decision history; do not treat as current guidance):

- [ADR 0001: Workspace-first Mihomo MVP](docs/adr/0001-workspace-first-mihomo-mvp.md) — superseded by ADR 0005
- [ADR 0003: Versioned Claude rules with multi-target profiles](docs/adr/0003-versioned-claude-rules-with-multi-target-profiles.md) — superseded by ADR 0004
- [ADR 0005: Guided Profile publishing experience](docs/adr/0005-guided-profile-publishing-experience.md) — superseded by ADR 0010
- [ADR 0008: Route intent as the default customization boundary](docs/adr/0008-route-intent-as-default-customization-boundary.md) — superseded by ADR 0009
