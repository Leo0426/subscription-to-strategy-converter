# Subflow Strategy Builder — Domain Context

## Mission

A self-hosted Clash/Mihomo and Surge policy release control plane built around one consolidated `leo.yaml` template.

## Product Boundary

- Subflow owns policy intent, structural transformation, semantic validation, target-specific releases, and subscription lifecycle.
- Protocol parsing and broad format conversion are compatibility inputs, not the product's differentiating capability.
- Clash/Mihomo is the semantic quality target; Surge is a public compatibility target with explicit warnings for skipped protocols and rule sets.
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
| **Leo Template** | The single supported configuration at `community_templates/leo/leo.yaml`; it supplies DNS/TUN, proxy groups, providers and the full ordered rule graph |
| **PolicyPreset** | A small product-facing, named starting policy graph that is copied into a Profile and may then be freely composed |
| **RulePack** | A selectable product module containing one business target group, its dependent groups, and the concrete ordered rules that route to it |
| **RulePackSelection** | The ordered set of RulePack identifiers chosen by a user and compiled into a PolicySnapshot |
| **PolicySnapshot** | The complete `SelectedPolicy` stored in a Profile after preset selection or custom composition; later preset changes do not mutate it |
| **PolicyWorkspace** | Product core for the MVP: an in-memory policy workspace holding nodes, groups, rules, providers, settings, graph data, analyzer findings, simulator traces, and compile output |
| **PolicyWorkbench** | The single-page product surface that combines source connection, RulePackSelection, optional RouteIntent overrides, validation, and Profile publication |
| **Profile** | A mutable policy intent containing one authorized source, ServiceRoutes, and target-specific publication choices |
| **ServiceRoute** | One entry in a RouteIntent that maps a catalog service to a primary NodePool, optional fallback NodePool, and final target |
| **RuleSource** | A policy-rule input identified by its origin, format, version, and content digest |
| **ProfileRevision** | An immutable snapshot of one Profile intent used as the input to validation and publication |
| **Release** | An immutable, validated set of stored target artifacts and provenance produced from one ProfileRevision and eligible for publication or rollback |
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
Clash/Mihomo artifact + Surge compatibility artifact
    ↓ immutable Release
Token-protected Subscription URLs
```

## Module Map

| Module | Role |
|--------|------|
| `app/ir.py` | All IR dataclasses: `ProxyNode`, `ProxyGroup`, `PolicyRule`, `RuleProvider`, `PolicyWorkspace`, graph/analysis/simulation types |
| `app/core/parser.py` | Raw Clash YAML parsing |
| `app/core/parsers/clash.py` | `clash_to_ir()` and `ir_to_clash_dict()` — bridge between Clash dicts and `ProxyNode` |
| `app/core/parsers/surge.py` | Parses supported Surge `[Proxy]` entries into `ProxyNode` while rejecting malformed recognized entries |
| `app/core/normalizer.py` | Post-parse dedup and normalization for `ProxyNode` lists |
| `app/core/fetcher.py` | HTTP fetching with SSRF safety checks |
| `app/core/subconverter.py` | Calls `tindy2013/subconverter` to convert raw subscriptions to Clash YAML |
| `app/core/subscription.py` | `load_subscription()` — end-to-end: URL → Clash YAML or Surge config → normalized `ProxyNode` list |
| `app/core/template_engine.py` | Built-in preset definitions, local template loader, `apply_template()`, `list_templates()` |
| `app/core/powerfullz.py` | Fetches powerfullz static YAML from jsDelivr CDN |
| `app/core/policy_workspace.py` | Workspace conversion boundary: `config_to_workspace()`, `workspace_from_dict()`, `workspace_to_mihomo_config()`, `compile_mihomo_config()` |
| `app/core/policy_graph.py` | `build_policy_graph()` → `PolicyGraph` (nodes + edges) |
| `app/core/policy_analyzer.py` | `analyze_workspace()` → `list[AnalyzerFinding]` |
| `app/core/policy_simulator.py` | `simulate_destination()` → `SimulationTrace` |
| `app/core/policy_catalog.py` | Extracts and deduplicates policy entries across community templates |
| `app/core/rule_packs.py` | Exposes concrete business rule cards and assembles a RulePackSelection into a PolicySnapshot |
| `app/core/intent_compiler.py` | Compiles product-facing NodePools and ServiceRoutes into NodeSelectors, ProxyGroups, and ordered rules |
| `app/core/template_policy_transform.py` | ServiceRoute transformation boundary with Claude template analysis and compatibility adapters |
| `app/core/profiles.py` | Persistent Profile store with token authorization and last-successful artifact caching |
| `app/core/renderer.py` | `render_yaml()` — serializes a dict to YAML string |
| `app/core/platforms/surge.py` | Public Surge compatibility compiler; reports skipped protocols and unsupported MRS rule sets |
| `app/core/platforms/singbox.py` | Experimental sing-box compiler |
| `app/core/sessions.py` | In-memory session store for large policy payloads (avoids huge query strings in `/subscribe`) |
| `app/core/config_tree.py` | Preview tree builder for raw Clash config |
| `app/api/convert.py` | Main API router — workspace, convert, subscribe, simulate, compile endpoints |
| `app/api/community.py` | Community template catalog API |
| `app/api/health.py` | Health check |
| `app/api/system.py` | Route Control Room dependency status API for app, Profile DB, and subconverter |
| `app/models/` | Pydantic request/response models |

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
| GET | `/system/status` | App, Profile DB, and subconverter status for the Route Control Room |
| GET | `/templates` | Return the single supported Leo template |
| GET | `/templates/detail` | Leo template structure and YAML preview; other template IDs are rejected |
| GET | `/policy-catalog` | Extracted rule providers across community templates |
| GET | `/rule-packs` | List selectable RulePacks, concrete rules, dependencies, categories, and preset defaults |
| GET | `/intent/catalog` | List supported service and region choices for RouteIntent editors |
| GET | `/subconverter/targets` | All supported output targets |
| POST | `/preview` | Parse subscription → node list + config tree |
| POST | `/convert` | Full conversion → rendered config string |
| POST | `/workspace/preview` | Build workspace + graph + analyzer findings |
| POST | `/render` | Render one target from a structured ConvertRequest body without query-string size limits |
| GET | `/claude/templates` | List templates containing Claude policy with target-specific compatibility metadata |
| POST | `/analyze` | Re-analyze an existing workspace dict |
| POST | `/simulate` | Simulate a destination through workspace rules |
| POST | `/compile/mihomo` | Compile workspace dict → Mihomo YAML |
| POST | `/session` | Store large policy payload, return session ID |
| POST | `/profiles` | Persist one Leo-based Profile and return token-protected Clash and Surge Subscription URLs |
| GET | `/profiles` | List redacted local Profile summaries without token or source subscription URL |
| GET | `/profiles/{profile_id}/draft` | Read an editable Profile conversion intent with token authorization |
| PUT | `/profiles/{profile_id}` | Replace a Profile conversion intent with token authorization and invalidate old artifacts |
| GET | `/subscribe/{profile_id}` | Compile a persisted Profile for `target=clash|mihomo|surge` or return its target-specific stale artifact |
| GET | `/subscribe` | Stable URL for proxy clients — returns config directly |

## Platform Support

| Platform | Priority | Compiler |
|----------|----------|---------|
| Mihomo / Clash | Product semantic quality bar | `app/core/policy_workspace.py` → `workspace_to_mihomo_config()` + `app/core/renderer.py` |
| Surge | Public compatibility target; unsupported protocols and MRS sources are skipped with warnings | `app/core/platforms/surge.py` |
| sing-box | Internal experimental compiler; rejected by Leo-backed product interfaces | `app/core/platforms/singbox.py` |

## Key Invariants

- `ProxyNode` is the only internal representation of a proxy — never pass raw dicts across module boundaries
- Shadowsocks transport options required for connectivity, including Surge `obfs` and `obfs-host`, must survive input normalization and map to the equivalent target-client syntax
- Mihomo output from `/convert` and `/subscribe` must compile through `PolicyWorkspace` via `compile_mihomo_config()`
- Mihomo is the first quality-bar compiler; other compilers remain experimental until semantic parity is explicit
- Experimental compilers should report unsupported protocols without breaking the workspace loop
- `RULE-SET` in Surge uses a direct URL (not provider name); the compiler resolves the name via `rule_providers` dict
- Surge does not accept Mihomo-only rule types such as `DOMAIN-REGEX`, `PROCESS-NAME-REGEX`, and `IN-NAME`; the compatibility compiler must skip and report them rather than emit an invalid `.conf` line
- Community templates live under `community_templates/` (scanned root); the deduplicated community template is `community_templates/leo/leo.yaml`
- All template IDs from the community are prefixed `local:` (e.g. `local:community_templates/leo/leo.yaml`)
- Sessions in `app/core/sessions.py` are in-memory only; they do not persist across restarts
- Profiles persist in SQLite; access requires both the profile ID and an independent token whose hash is stored in the database
- A Profile has one source subscription, a PolicySnapshot, and target-specific Clash/Mihomo and Surge publications
- A Profile may serve its last successful artifact only for an external source dependency failure and must mark it with `X-Subflow-Stale: true`
- Updating a Profile invalidates all previously compiled artifacts before the new intent can be served
- A TemplatePolicyTransform must preserve provider URLs, rule order, DNS/TUN settings, and every non-Claude policy edge
- Claude customization requires a recognizable Claude rule/provider in the selected template; it never injects an application-owned domain list
- Surge direct service transforms fail closed when they require incompatible template semantics; normal Profile compilation is best-effort and reports skipped protocols and MRS rule sets through warnings
- Protocol and client breadth must not bypass `PolicyWorkspace` or duplicate a mature conversion engine without a demonstrated semantic requirement
- New service-specific routing capabilities extend `ServiceRoute`; they must not introduce a parallel Profile or publishing lifecycle
- `SelectedPolicy.mode=merge` is additive for legacy callers; the structured composer uses `replace` to own proxy groups, rule providers, and ordered rules as one validated policy graph
- New product Profiles and public conversion interfaces use exactly `local:community_templates/leo/leo.yaml`; other template IDs fail validation
- RulePackSelection is the default product customization boundary; PolicyPreset only supplies a default selection and never prevents individual card changes
- RouteIntent is an optional egress override for selected RulePacks; its NodePools compile into NodeSelectors and its ServiceRoutes replace the corresponding target-group members
- Expert composition replaces the complete PolicySnapshot and does not combine implicitly with RouteIntent changes
- PolicyWorkbench exposes the common creation path on one page; RouteIntent controls and the complete PolicySnapshot composer are progressively disclosed rather than separate steps or pages
- A stored PolicySnapshot does not automatically merge later PolicyPreset changes; updating from a preset is an explicit reset operation
- `NodeSelector` references are expanded against the latest upstream `ProxyNode` inventory on every preview/render/Profile subscription request; unknown selectors fail closed and selectors producing an empty group are publish-blocking errors
- Rules after the first `MATCH` or `FINAL` are unreachable and must be reported by the analyzer
- A publishable Release stores immutable target artifacts plus the source-content digest, ProfileRevision, template identity, rule-source identity, and compiler/transformer versions that produced them
- Rollback selects a previously validated Release; it does not rebuild that Release from mutable upstream dependencies

## ADRs

- [ADR 0001: Workspace-first Mihomo MVP](docs/adr/0001-workspace-first-mihomo-mvp.md)
- [ADR 0002: Persistent profiles and stale fallback](docs/adr/0002-persistent-profiles-and-stale-fallback.md)
- [ADR 0003: Versioned Claude rules with multi-target profiles](docs/adr/0003-versioned-claude-rules-with-multi-target-profiles.md)
- [ADR 0004: Template-driven Claude policy transforms](docs/adr/0004-template-driven-claude-policy-transforms.md)
- [ADR 0005: Guided Profile publishing experience](docs/adr/0005-guided-profile-publishing-experience.md)
- [ADR 0006: Policy release control plane over protocol conversion](docs/adr/0006-policy-release-control-plane-over-protocol-conversion.md)
- [ADR 0007: One canonical base with composable policy presets](docs/adr/0007-one-canonical-base-with-composable-policy-presets.md)
- [ADR 0008: Route intent as the default customization boundary](docs/adr/0008-route-intent-as-default-customization-boundary.md)
- [ADR 0009: Rule packs as the default assembly boundary](docs/adr/0009-rule-packs-as-default-assembly-boundary.md)
- [ADR 0010: Single-page policy workbench](docs/adr/0010-single-page-policy-workbench.md)
