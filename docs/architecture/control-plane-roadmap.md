# Control Plane Roadmap

## Product North Star

This project is not a Clash config generator. It is an early Traffic Policy Control Plane for designing, analyzing, simulating, compiling, and publishing cross-platform proxy policy.

The durable asset is the Policy IR. YAML, JSON, `.conf`, and client-specific package formats are compiler targets.

## Current Baseline

- FastAPI service with a browser UI.
- Authorized subscription fetching with URL safety checks.
- Clash YAML subscription parsing into `ProxyNode`.
- Template-driven Mihomo YAML rendering.
- Experimental sing-box rendering.
- Local and community Mihomo template catalog.
- Policy catalog extraction and deduplication for existing templates.
- Basic config tree preview for proxies, groups, rules, and rule providers.

## MVP Architecture

```text
Subscription / Templates / Provider Catalog
                 |
                 v
        Policy Workspace Builder
                 |
                 v
             Policy IR
                 |
    +------------+-------------+-------------+
    |            |             |             |
    v            v             v             v
 Analyzer   Graph Builder   Simulator   Mihomo Compiler
    |            |             |             |
    v            v             v             v
 Findings     DAG JSON       Trace       YAML
```

## Module Boundaries

### Source Layer

Owns external inputs: subscription URLs, local templates, community templates, and provider catalog entries.

It should hide URL normalization, mirror handling, template scanning, and upstream metadata extraction.

### IR Layer

Owns stable in-memory and JSON-serializable policy objects.

It should hide platform-specific syntax and expose semantic concepts: proxy node, provider, rule, group, edge, target, and workspace.

### Graph Layer

Owns policy graph derivation.

It should not mutate policy. Its output should be a frontend-friendly graph with stable node ids and edge ids.

### Analysis Layer

Owns deterministic static checks.

It should return structured findings, not prose-only diagnostics.

### Simulation Layer

Owns single-request traffic tracing.

It should explain why a destination matches a rule and how that rule resolves through groups to a node or builtin target.

### Compiler Layer

Owns target-specific lowering.

For MVP, Mihomo is the quality bar. Other renderers should be treated as experimental until they pass the same IR, analyzer, simulator, and golden-output tests.

## Milestones

### M1: Policy IR Foundation

- Define `PolicyWorkspace`, `PolicyRule`, `RuleProvider`, `ProxyGroup`, and `PolicyEdge`.
- Convert current template output into Policy IR.
- Add round-trip tests: subscription plus template becomes Policy IR, then Mihomo dict.

### M2: Analyzer MVP

- Detect missing provider references.
- Detect missing group targets.
- Detect duplicate rules.
- Detect group cycles.
- Detect unreachable groups.
- Return findings with stable `code`, `severity`, `message`, and `path`.

### M3: Simulator MVP

- Support `DOMAIN`, `DOMAIN-SUFFIX`, `DOMAIN-KEYWORD`, `IP-CIDR`, `GEOIP`, `RULE-SET`, and `MATCH` at MVP depth.
- Return ordered rule trace and resolved target trace.
- Expose `POST /simulate`.

### M4: Visual Graph MVP

- Build graph JSON from Policy IR.
- Show providers, rules, proxy groups, proxy nodes, and builtin targets.
- Add highlighting from analyzer findings and simulator traces.

### M5: Marketplace MVP

- Promote the existing template and provider extraction into a first-class local marketplace.
- Add categories such as AI, Media, Game, Developer, Apple, Google, Crypto, CDN, and Speedtest.
- Track upstream URL, canonical id, source template, behavior, format, and target group hints.

### M6: Compiler Hardening

- Make Mihomo compiler the canonical output path.
- Add golden YAML tests for minimal, developer, provider-heavy, and custom-group workspaces.
- Keep sing-box behind an experimental label until semantic parity is explicit.

## Future Tracks

- Multi-target renderers: Surge, sing-box, Quantumult X, Loon, OpenClash packages.
- Rule AST: logical rules, boolean composition, domain/IP trie optimization.
- Optimizer passes: dedup, suffix folding, provider merge, rule reorder, regex optimization.
- Collaboration: workspace persistence, versioning, review, rollback, audit.
- GitOps: compile in CI and publish subscription artifacts.
- Runtime dashboard: rule hit counts, group usage, latency, DNS cache, provider health.
- Policy DSL: a higher-level authoring interface that compiles into Policy IR.
- AI assist: generate rule bundles, explain traces, and suggest conflict fixes.
