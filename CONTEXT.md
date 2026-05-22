# Subflow Strategy Builder — Domain Context

## Mission

A visual proxy policy workspace builder that replaces manual YAML editing with a structured workspace. Users subscribe once, inspect and simulate policy behavior, then compile a trusted Mihomo configuration.

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
| **ProxyGroup** | A named group of nodes or groups with a dispatch strategy (select / url-test / fallback / load-balance) |
| **RuleProvider** | An external rule-set URL referenced by name in rules (Clash: `rule-providers`) |
| **Template** | A community-contributed or built-in YAML skeleton providing proxy-groups, rules, rule-providers — loaded via `load_any_template()` |
| **Compiler** | A platform-specific module (`surge.py`, `singbox.py`) that takes (nodes, groups, rules, providers) → formatted config string |
| **Subscription URL** | The stable `/subscribe?...` endpoint URL users paste into their proxy client |
| **MATCH / FINAL** | Catch-all rule — Clash calls it `MATCH`, Surge calls it `FINAL` |
| **MRS** | Mihomo binary rule-set format; must be substituted with `.txt` URLs for Surge |

## Architecture Layers

```
Subscription URL (Clash YAML)
    ↓ parse
ProxyNode IR list
    ↓ apply_template + custom strategy
PolicyWorkspace (groups, rules, providers)
    ↓ analyze + simulate + visualize
Mihomo Compiler
    ↓
Mihomo YAML
```

## Platform Support

| Platform | Priority | Compiler |
|----------|----------|---------|
| Mihomo / Clash | MVP quality bar | `app/core/renderer.py` |
| Surge (macOS) | Experimental | `app/core/platforms/surge.py` |
| sing-box | Experimental | `app/core/platforms/singbox.py` |

## Key Invariants

- `ProxyNode` is the only internal representation of a proxy — never pass raw dicts across module boundaries
- Mihomo is the first quality-bar compiler; other compilers remain experimental until semantic parity is explicit
- Experimental compilers should report unsupported protocols without breaking the workspace loop
- `RULE-SET` in Surge uses a direct URL (not provider name); the compiler resolves the name via `rule_providers` dict
- Community templates live under `community_templates/THEYAMLS/` and are scanned at startup; built-in templates are gone except `powerfullz`
- All template IDs from the community are prefixed `local:` (e.g. `local:community_templates/THEYAMLS/...`)

## ADRs

- [ADR 0001: Workspace-first Mihomo MVP](docs/adr/0001-workspace-first-mihomo-mvp.md)
