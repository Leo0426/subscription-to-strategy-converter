# Subflow Strategy Builder — Domain Context

## Mission

A guided proxy Profile publisher that replaces manual YAML editing with an auditable conversion flow. Users import one subscription, choose target-specific templates, validate policy behavior through a shared workspace model, and publish durable client subscription URLs.

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
| **PolicyWorkspace** | Product core for the MVP: an in-memory policy workspace holding nodes, groups, rules, providers, settings, graph data, analyzer findings, simulator traces, and compile output |
| **Profile** | A durable conversion intent containing one source subscription, one Claude Egress, target-specific Clash/Surge templates, token-protected Subscription URLs, and a last successful compile artifact per target |
| **ProxyGroup** | A named group of nodes or groups with a dispatch strategy (select / url-test / fallback / load-balance) |
| **RuleProvider** | An external rule-set URL referenced by name in rules (Clash: `rule-providers`) |
| **TemplatePolicyTransform** | A structure-aware operation that preserves a selected template and changes only a recognized service-policy subgraph |
| **Claude Egress** | The explicit node or policy group placed first in the template's dedicated Claude policy group |
| **Template** | A community-contributed or built-in YAML skeleton providing proxy-groups, rules, rule-providers — loaded via `load_any_template()` |
| **Compiler** | A platform-specific module (`surge.py`, `singbox.py`) that takes (nodes, groups, rules, providers) → formatted config string |
| **Subscription URL** | The stable `/subscribe?...` endpoint URL users paste into their proxy client |
| **MATCH / FINAL** | Catch-all rule — Clash calls it `MATCH`, Surge calls it `FINAL` |
| **MRS** | Mihomo binary rule-set format; must be substituted with `.txt` URLs for Surge |

## Architecture Layers

```
Subscription URL
    ↓ subconverter (tindy2013/subconverter)
Clash YAML
    ↓ parse + normalize
ProxyNode IR list
    ↓ apply_template + custom strategy
PolicyWorkspace (groups, rules, providers, settings)
    ↓ analyze + simulate + visualize
Mihomo Compiler
    ↓
Mihomo YAML
```

## Module Map

| Module | Role |
|--------|------|
| `app/ir.py` | All IR dataclasses: `ProxyNode`, `ProxyGroup`, `PolicyRule`, `RuleProvider`, `PolicyWorkspace`, graph/analysis/simulation types |
| `app/core/parser.py` | Raw Clash YAML parsing |
| `app/core/parsers/clash.py` | `clash_to_ir()` and `ir_to_clash_dict()` — bridge between Clash dicts and `ProxyNode` |
| `app/core/normalizer.py` | Post-parse dedup and normalization for `ProxyNode` lists |
| `app/core/fetcher.py` | HTTP fetching with SSRF safety checks |
| `app/core/subconverter.py` | Calls `tindy2013/subconverter` to convert raw subscriptions to Clash YAML |
| `app/core/subscription.py` | `load_subscription()` — end-to-end: URL → subconverter → Clash YAML → `ProxyNode` list |
| `app/core/template_engine.py` | Built-in preset definitions, local template loader, `apply_template()`, `list_templates()` |
| `app/core/powerfullz.py` | Fetches powerfullz static YAML from jsDelivr CDN |
| `app/core/policy_workspace.py` | Workspace conversion boundary: `config_to_workspace()`, `workspace_from_dict()`, `workspace_to_mihomo_config()`, `compile_mihomo_config()` |
| `app/core/policy_graph.py` | `build_policy_graph()` → `PolicyGraph` (nodes + edges) |
| `app/core/policy_analyzer.py` | `analyze_workspace()` → `list[AnalyzerFinding]` |
| `app/core/policy_simulator.py` | `simulate_destination()` → `SimulationTrace` |
| `app/core/policy_catalog.py` | Extracts and deduplicates policy entries across community templates |
| `app/core/template_policy_transform.py` | Claude template analysis, compatibility reporting, and policy-subgraph transformation boundary |
| `app/core/profiles.py` | Persistent Profile store with token authorization and last-successful artifact caching |
| `app/core/renderer.py` | `render_yaml()` — serializes a dict to YAML string |
| `app/core/platforms/surge.py` | Experimental Surge compiler |
| `app/core/platforms/singbox.py` | Experimental sing-box compiler |
| `app/core/sessions.py` | In-memory session store for large policy payloads (avoids huge query strings in `/subscribe`) |
| `app/core/config_tree.py` | Preview tree builder for raw Clash config |
| `app/api/convert.py` | Main API router — workspace, convert, subscribe, simulate, compile endpoints |
| `app/api/community.py` | Community template catalog API |
| `app/api/health.py` | Health check |
| `app/api/system.py` | Route Control Room dependency status API for app, Profile DB, and subconverter |
| `app/models/` | Pydantic request/response models |

## Built-in Templates

Preset templates are code-defined in `app/core/template_engine.py::PRESET_TEMPLATES`:

| ID | Description |
|----|-------------|
| `minimal` | Core groups only: Proxy / Auto / Fallback / DIRECT |
| `developer` | GitHub, npm, Docker, JetBrains, Microsoft, Apple splits |
| `ai-tools` | Claude, OpenAI, Gemini, Perplexity, Cursor, GitHub Copilot splits |
| `streaming` | Netflix, YouTube, Disney, Spotify, Telegram splits |
| `full` | AI + Developer + Streaming + geo groups (HK / SG / JP / US) |
| `powerfullz` | powerfullz/override-rules static YAML, fetched from jsDelivr at request time |

Community templates are auto-scanned from `community_templates/THEYAMLS/**/*.yaml` at startup and served with IDs prefixed `local:community_templates/...`.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/system/status` | App, Profile DB, and subconverter status for the Route Control Room |
| GET | `/templates` | List all available templates |
| GET | `/templates/detail` | Template structure and YAML preview |
| GET | `/policy-catalog` | Extracted rule providers across community templates |
| GET | `/subconverter/targets` | All supported output targets |
| POST | `/preview` | Parse subscription → node list + config tree |
| POST | `/convert` | Full conversion → rendered config string |
| POST | `/workspace/preview` | Build workspace + graph + analyzer findings |
| GET | `/claude/templates` | List templates containing Claude policy with target-specific compatibility metadata |
| POST | `/analyze` | Re-analyze an existing workspace dict |
| POST | `/simulate` | Simulate a destination through workspace rules |
| POST | `/compile/mihomo` | Compile workspace dict → Mihomo YAML |
| POST | `/session` | Store large policy payload, return session ID |
| POST | `/profiles` | Persist one platform-neutral Profile and return token-protected Clash and Surge Subscription URLs |
| GET | `/profiles` | List redacted local Profile summaries without token or source subscription URL |
| GET | `/profiles/{profile_id}/draft` | Read an editable Profile conversion intent with token authorization |
| PUT | `/profiles/{profile_id}` | Replace a Profile conversion intent with token authorization and invalidate old artifacts |
| GET | `/subscribe/{profile_id}` | Compile a persisted Profile for `target=clash|surge` or return that target's stale artifact on an external dependency failure |
| GET | `/subscribe` | Stable URL for proxy clients — returns config directly |

## Platform Support

| Platform | Priority | Compiler |
|----------|----------|---------|
| Mihomo / Clash | MVP quality bar | `app/core/policy_workspace.py` → `workspace_to_mihomo_config()` + `app/core/renderer.py` |
| Surge (macOS) | Compatible Claude-template flow supported; general parity experimental | `app/core/platforms/surge.py` |
| sing-box | Experimental | `app/core/platforms/singbox.py` |

## Key Invariants

- `ProxyNode` is the only internal representation of a proxy — never pass raw dicts across module boundaries
- Mihomo output from `/convert` and `/subscribe` must compile through `PolicyWorkspace` via `compile_mihomo_config()`
- Mihomo is the first quality-bar compiler; other compilers remain experimental until semantic parity is explicit
- Experimental compilers should report unsupported protocols without breaking the workspace loop
- `RULE-SET` in Surge uses a direct URL (not provider name); the compiler resolves the name via `rule_providers` dict
- Community templates live under `community_templates/` (scanned root) and `community_templates/THEYAMLS/` (YAML templates); `community_templates/Overwrite/` contains non-template formats (OpenClash overrides) that are intentionally excluded
- All template IDs from the community are prefixed `local:` (e.g. `local:community_templates/THEYAMLS/...`)
- Sessions in `app/core/sessions.py` are in-memory only; they do not persist across restarts
- Profiles persist in SQLite; access requires both the profile ID and an independent token whose hash is stored in the database
- A Profile shares one source subscription and Claude Egress, selects templates independently for Clash and Surge, and caches artifacts by target
- A Profile may serve a target's last successful artifact only for an external source dependency failure and must mark it with `X-Subflow-Stale: true`
- Updating a Profile invalidates all previously compiled artifacts before the new intent can be served
- A TemplatePolicyTransform must preserve provider URLs, rule order, DNS/TUN settings, and every non-Claude policy edge
- Claude customization requires a recognizable Claude rule/provider in the selected template; it never injects an application-owned domain list
- Surge template compilation fails closed when the complete rule/provider graph or node protocols cannot be represented without substitution

## ADRs

- [ADR 0001: Workspace-first Mihomo MVP](docs/adr/0001-workspace-first-mihomo-mvp.md)
- [ADR 0002: Persistent profiles and stale fallback](docs/adr/0002-persistent-profiles-and-stale-fallback.md)
- [ADR 0003: Versioned Claude rules with multi-target profiles](docs/adr/0003-versioned-claude-rules-with-multi-target-profiles.md)
- [ADR 0004: Template-driven Claude policy transforms](docs/adr/0004-template-driven-claude-policy-transforms.md)
- [ADR 0005: Guided Profile publishing experience](docs/adr/0005-guided-profile-publishing-experience.md)
