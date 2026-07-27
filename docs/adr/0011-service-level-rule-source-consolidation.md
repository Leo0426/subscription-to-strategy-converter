# Service-level rule source consolidation

Status: accepted

Subflow compresses the Leo template's rule supply chain by consolidating each service to one best RuleSource, accepting controlled behavior change, because zero-behavior-change deduplication cannot reach the shared budgets (≤ 200 providers, ≤ 16 MiB cold-start download) and the current 508-source graph already routes 7268 entries by accidental rule order rather than intent.

## Considered Options

### Zero-behavior-change compression only

- Benefits: machine-provable that compiled output routing never changes; no regression risk.
- Costs: exact-duplicate groups (18) and full-containment subsumption reach roughly 460-470 sources, leaving the count budget 2.3x exceeded and the byte budget still violated; the supply-chain surface (33 upstreams, 47 proxied intermediaries) stays intact.

### Service-level consolidation to one best source (chosen)

- Benefits: reaches the budgets; each service's routing is defined by one deliberately chosen upstream instead of the order-dependent overlap of several; shrinks the trust surface toward the admission checklist's preferred upstreams.
- Costs: concrete domain sets change, so behavior changes; requires an explicit best-source ranking, per-service regression evidence, and batched reversible commits.

### Application-owned merged rule lists

- Benefits: maximal compression and full control of content.
- Costs: makes Subflow a rule publisher, violating the product boundary (ADR 0006) and the invariant that the application never injects its own domain lists; rejected.

## Best-source ranking

For each consolidation unit (target group × service), retained source is chosen by, in order:

1. Dual-target usability: a source Surge can also consume (text/yaml, or with a text equivalent) outranks an MRS-only source.
2. Trusted upstream: sources from the admission checklist's preferred upstreams (MetaCubeX, blackmatrix7, DustinWin, ruleset.skk.moe, …) outrank others; direct origin outranks third-party proxy fronts.
3. Coverage: higher unique normalized entry count wins.
4. Cost: smaller byte count wins on ties.

## Controlled behavior change

- Before each batch, a per-service key-domain list (10-20 representative domains per service group) is simulated against the workspace; after consolidation the same list must route to the same target groups. Losing duplicate coverage is acceptable; a key domain drifting to the catch-all is not.
- Both Mihomo and Surge artifacts must compile, and the Surge compiler must not lose a service it previously emitted rules for.
- Consolidation lands as one commit per service batch so any batch can be reverted independently.
- The audit snapshot and score are regenerated after each batch; the consolidation is complete when provider count ≤ 200 and cold-start bytes ≤ 16 MiB.

## Consequences

- The audit tooling gains a consolidation planner that groups providers by target and service family, ranks them by the order above, and emits a reviewable removal plan before any edit.
- Deletion policy is extended: cross-run-confirmed unusability remains the standard for availability-based removal; this ADR adds intent-based removal justified by the ranking plus key-domain regression evidence.
- Some long-tail domains covered only by removed sources will fall through to broader GEOSITE/GEOIP rules or the default proxy; this is accepted and traded for a predictable, auditable supply chain.
- Future source additions continue through the admission checklist; adding a second source for an already-consolidated service requires stating why one source is insufficient.
