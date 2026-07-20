# Single-page policy workbench

Status: accepted

Subflow replaces the four-step creation wizard with one `PolicyWorkbench`, because rule-card selection is reversible and understandable enough that forced step transitions add navigation state without protecting a meaningful invariant. Source connection, RulePack selection, validation and publication remain visible together; RouteIntent and expert composition use progressive disclosure.

## Considered Options

### Keep the four-step wizard

- Benefits: presents one decision at a time and can enforce a strict sequence.
- Costs: hides context, makes users move backward to compare choices, duplicates navigation state, and treats reversible card selection as a gated decision.

### Put every control on one flat page

- Benefits: no navigation and maximum discoverability.
- Costs: node pools, service fallbacks and full policy composition overwhelm the common task.

### Use one page with progressive disclosure

- Benefits: the complete common path is visible, card changes remain immediate, advanced controls stay available without becoming prerequisites, and validation is an explicit user action.
- Costs: the page can become long when all rule details are expanded and requires disciplined visual hierarchy.

## Consequences

- There is no step navigation, unlock state, next/back action, or automatic page transition during Profile creation.
- The primary page has three sections: source, rules, and validate/publish.
- RouteIntent editing is a collapsed advanced section; complete PolicySnapshot editing remains in the contextual inspector.
- Rule-card changes do not trigger background policy compilation; compilation occurs on explicit validation or expert-mode entry.
- Validation must be repeated after any source, card, or egress change before publication is enabled.
