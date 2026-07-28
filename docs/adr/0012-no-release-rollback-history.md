# No Release/ProfileRevision rollback history

Status: accepted

Subflow will not build the immutable Release/ProfileRevision/provenance model that ADR 0006 anticipated ("must become separate concepts so validation, publication, history, and rollback are reproducible"), because the single self-hosted operator's actual risk is external-dependency staleness, which ADR 0002's last-successful-artifact cache already covers; a full version history and rollback surface would add storage, API surface, and CONTEXT vocabulary that the personal-deployment MVP has no concrete use for today.

## Considered Options

### Build the full Release/ProfileRevision model as ADR 0006 anticipated
- Benefits: immutable audit trail, rollback to any prior validated Release, provenance for every artifact.
- Costs: new storage schema, new API surface, and two new domain concepts to design and maintain for a single-operator deployment that has never needed them.

### Keep the current Profile + last-successful-artifact cache only (chosen)
- Benefits: no new concepts or storage; ADR 0002's stale-fallback already protects the one risk that occurs in practice (an external dependency failing).
- Costs: an operator who edits their own Profile into a broken state has no built-in way back except reconstructing the prior request by hand.

## Consequences

- `Release` and `ProfileRevision` are removed from CONTEXT.md's domain vocabulary and Key Invariants; they are not implemented and are not planned.
- ADR 0006 remains accepted for its other decisions (policy release control plane over protocol conversion, reusing a mature converter at the compatibility boundary, `ServiceRoute` generalization); only its Release/ProfileRevision/rollback consequence is withdrawn by this ADR.
- If a concrete need for rollback emerges later (e.g. a bad self-edit with no way back), it should be scoped as its own minimal decision rather than reviving the original provenance-heavy design.
