# Versioned Claude rules with multi-target Profiles

Status: superseded by ADR 0004

Subflow must turn one authorized subscription into reliable Claude routing for both Clash/Mihomo and Surge without duplicating user intent. We will model Claude routing as a built-in, versioned `ServiceRulePack`, route every Claude service surface through one explicit `Claude Egress`, and persist one platform-neutral Profile that compiles and caches artifacts independently per target. This keeps policy meaning in `PolicyWorkspace`, isolates platform differences in compilers, and prevents runtime rule downloads or duplicated Profiles from silently changing behavior.

## Considered Options

Built-in ServiceRulePack with one Claude Egress:
- Benefits: deterministic output, consistent session identity, shared semantics across clients, reviewable rule changes.
- Costs: domain coverage advances through Subflow releases and may need user extensions between releases.

Remote community rule providers:
- Benefits: faster domain updates and less application maintenance.
- Costs: runtime supply-chain risk, non-reproducible output, and Surge provider-format compatibility failures.

Separate Profiles per target:
- Benefits: smaller migration from the existing single-target model.
- Costs: duplicated subscription secrets, tokens, policy edits, and stale artifacts that can drift independently.

Automatic region or latency-based Claude egress:
- Benefits: less user configuration.
- Costs: node names and latency do not prove Claude availability, and automatic rotation can break session consistency.

## Consequences

- Subscription preview must let the user explicitly select a node or policy group as Claude Egress.
- A Profile returns target-specific subscription URLs while the legacy URL continues to default to Mihomo.
- Cached artifacts are keyed by target; a stale Surge response never substitutes a Clash/Mihomo artifact or vice versa.
- Surge becomes a supported output for the scoped Claude ServiceRulePack flow, while ADR 0001 still treats general Surge semantic parity as experimental.
- Core Claude rules include only Anthropic/Claude-specific domains and carry service-surface metadata; users may persist additional rules in the Profile.
