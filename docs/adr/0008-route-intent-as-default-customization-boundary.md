# Route intent as the default customization boundary

Status: superseded by 0009

Subflow makes `RouteIntent`—reusable `NodePool` definitions plus service primary/fallback choices—the default customization boundary, because most users want to express routing outcomes rather than manually maintain a complete proxy-group and rule graph. The existing complete `PolicySnapshot` composer remains available as an explicit expert mode.

## Considered Options

### Expose only the complete policy graph

- Benefits: maximum expressiveness with one editor.
- Costs: every service change requires understanding NodeSelectors, group references, providers, and rule order; common tasks carry expert-level cognitive cost.

### Add more complete scenario presets

- Benefits: fast for cases matching a preset exactly.
- Costs: preset count grows combinatorially with region and service preferences, while small variations still force full composition.

### Compile route intent into the complete policy graph

- Benefits: keeps the common interface small, reuses NodePools across services, preserves one target-independent PolicySnapshot and existing validation/compilation paths.
- Costs: the service catalog and intent compiler become product-owned policy inputs, and switching to expert mode must have explicit ownership semantics.

## Consequences

- The guided flow defaults to `service → primary NodePool → optional fallback NodePool → final target`.
- Services not enabled in RouteIntent continue to use the selected PolicyPreset.
- RouteIntent compiles before PolicyWorkspace analysis and Clash/Surge target compilation.
- Profiles persist both RouteIntent for future editing and its compiled PolicySnapshot for deterministic subscription behavior.
- Expert mode takes ownership of the complete PolicySnapshot; RouteIntent is not implicitly merged afterward.
- New services extend the service catalog and compiler tests instead of adding a new product workflow.
