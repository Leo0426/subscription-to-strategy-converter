# Rule packs as the default assembly boundary

Status: accepted

Subflow makes selectable `RulePack` cards the default policy-assembly boundary, because users need to understand and choose which concrete rules enter the configuration before deciding how those rules exit. `RouteIntent` remains an optional egress override for selected cards, and the complete `PolicySnapshot` composer remains expert mode.

## Considered Options

### Keep RouteIntent as the first customization step

- Benefits: users can quickly express region and fallback preferences.
- Costs: users choose an exit before seeing which domains and groups will use it, while services already embedded in presets remain difficult to discover or remove.

### Expose the complete policy graph as cards

- Benefits: every group, provider and rule is directly controllable.
- Costs: foundation groups and dependency groups become selectable implementation details, allowing invalid partial graphs and recreating expert-mode cognitive load.

### Expose cohesive business RulePacks

- Benefits: each card is understandable and independently selectable, dependencies remain hidden but inspectable, presets become reversible bulk-selection shortcuts, and the compiler continues producing one complete PolicySnapshot.
- Costs: the product owns a stable RulePack catalog and must explicitly migrate or version cards when concrete rules change.

## Consequences

- Canonical foundation groups and common rules are always present and are shown as locked rather than selectable cards.
- Each business card displays its target group, dependent groups and complete concrete rules before selection.
- PolicyPreset provides only a default RulePackSelection; individual cards can always be added or removed.
- RouteIntent can override the target group for every selected RulePack, not a fixed service subset.
- Profiles persist RulePackSelection, optional RouteIntent and the compiled PolicySnapshot.
- Adding a business rule group requires one catalog card and compiler tests, not a new workflow or complete preset.
