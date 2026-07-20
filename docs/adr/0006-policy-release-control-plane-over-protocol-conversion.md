# Policy release control plane over protocol conversion

Status: accepted

Subflow will serve advanced personal users running a private local or self-hosted deployment and will differentiate through policy intent, semantic validation, reproducible releases, and rollback across Clash/Mihomo and Surge. It will reuse a mature converter such as subconverter at the protocol compatibility boundary instead of competing on the number of parsers and output formats, because duplicating protocol breadth would consume the project while leaving its `PolicyWorkspace`, analyzer, simulator, and Profile lifecycle without a distinct user outcome.

Claude routing is the first user-facing scenario, but service-specific behavior will enter the core through a platform-neutral `ServiceRoute`. Target compilers remain responsible for representing the same intent under each client's constraints, and unsupported semantics fail closed rather than being silently substituted.

## Considered Options

### Reimplement subconverter as a full conversion engine

- Benefits: one runtime and complete control over every parser and generator.
- Costs: permanent protocol-compatibility workload, slow parity, and little differentiation for users already served by subconverter.

### Provide a friendlier frontend for subconverter

- Benefits: small implementation surface and immediate usability improvement.
- Costs: weak product boundary; templates and query parameters remain the user model, while policy correctness and lifecycle remain unresolved.

### Build a policy release control plane over a conversion boundary

- Benefits: concentrates complexity behind `PolicyWorkspace`, makes changes explainable and testable, and turns Profile publication, validation, versioning, and rollback into one coherent workflow.
- Costs: supports fewer clients initially and requires explicit semantic compatibility tests for every quality-bar target.

## Consequences

- Protocol breadth is delegated to subconverter or another replaceable compatibility adapter; Subflow does not duplicate formats without a policy-semantic need.
- Clash/Mihomo is the general quality-bar target; Surge reaches the same bar only for explicitly supported ServiceRoute and template subsets, while unsupported semantics remain fail-closed.
- The next domain generalization is `ServiceRoute`; Claude-only request models become an adapter to that intent instead of the permanent core abstraction.
- Profile drafts, immutable ProfileRevisions, and Releases with stored artifacts and provenance must become separate concepts so validation, publication, history, and rollback are reproducible.
- Product success is measured by time to a validated publication, semantic parity, diagnosability, and rollback reliability—not template count or output-format count.
- Public multi-tenant conversion, billing, and a general-purpose YAML editor remain outside the current scope.
