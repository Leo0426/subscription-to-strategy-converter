# One canonical base with composable policy presets

Status: accepted

Subflow uses one product-owned Canonical Base Template and exposes a small catalog of PolicyPresets that become editable PolicySnapshots, because asking users and maintainers to choose and evolve multiple complete Clash/Surge templates duplicates settings, compatibility decisions, and policy graphs. Existing full templates remain available as import and API compatibility sources, but they are no longer the primary product model.

## Considered Options

### Maintain many complete templates

- Benefits: preserves familiar YAML artifacts and exposes every community variation directly.
- Costs: duplicates DNS/TUN settings and policy graphs, requires target-specific recommendation logic, and makes upgrades and user choices difficult to explain.

### Keep one base template without presets

- Benefits: smallest maintenance surface.
- Costs: forces every user to understand and construct the full policy graph before reaching a useful result.

### Use one canonical base with policy presets and composition

- Benefits: centralizes stable settings, gives new users meaningful starting points, and lets advanced users own a complete explicit policy graph without target-specific template selection.
- Costs: requires a compatibility boundary for existing template IDs and explicit snapshot semantics when presets evolve.

## Consequences

- The guided product selects one PolicyPreset, not separate Clash and Surge templates.
- Clash and Surge differences belong to target compilers and validation, not to product template selection.
- Selecting a preset copies its complete policy graph into the Profile as a PolicySnapshot; later preset changes never silently alter an existing Profile.
- Custom composition owns NodeSelectors, ProxyGroups, RuleProviders, and ordered rules through `SelectedPolicy.mode=replace`.
- Community and historical full templates remain readable through legacy APIs and may later feed an explicit import workflow.
