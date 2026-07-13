# Template-driven Claude policy transforms

Status: accepted

Subflow will customize the Claude policy already present in a selected community template instead of overlaying an application-owned domain list. A transform preserves the complete template and changes only its Claude policy subgraph: if a dedicated Claude group exists, the selected egress becomes its first member; if Claude rules currently share an AI/OpenAI group, the transform creates a dedicated Claude group that falls back to the original target and retargets only those Claude rules. Provider URLs, rule order, DNS/TUN settings, non-Claude rules, and all unrelated groups remain unchanged.

One Profile owns one source subscription and one Claude egress, but records a separate template for Clash and Surge. Clash can use every detected Claude template. Surge generation is allowed only when the selected template's complete rule/provider graph and node protocols can be compiled without semantic substitution; incompatible templates remain previewable with explicit reasons.

## Considered Options

Template-preserving Claude subgraph transform:
- Benefits: respects existing template authorship, preserves non-Claude behavior, and makes the customization boundary reviewable.
- Costs: requires structural analysis of heterogeneous community templates and explicit handling of unsupported shapes.

Built-in Claude domain overlay:
- Benefits: deterministic coverage and a uniform transform across templates.
- Costs: duplicates or overrides template policy, silently changes rule precedence, and does not satisfy template-based customization.

One shared template for both targets:
- Benefits: smaller Profile model and less UI state.
- Costs: many Clash rule-provider formats are not valid Surge rule-set inputs, so apparent parity would require unsafe substitutions.

Silent Surge provider replacement:
- Benefits: maximizes the number of templates that can produce a Surge file.
- Costs: changes the policy source and trust boundary without user intent, and can produce behavior that differs from the selected template.

## Consequences

- Template analysis exposes whether Claude policy is present, its current targets, whether a dedicated group exists, and target-specific compatibility reasons.
- A Claude transform must reject templates without a recognizable Claude policy instead of adding one.
- Surge generation is fail-closed: no built-in domains, provider URL rewriting, or third-party source replacement is allowed to make a template appear compatible.
- Profile subscription URLs remain target-specific and artifact caches remain isolated by target.
- The UI shows the selected Clash and Surge template explicitly and may synchronize them only when the same template is Surge-compatible.
- ADR 0003's multi-target Profile and target-isolated cache decisions remain, but its built-in `ServiceRulePack` decision is superseded.
