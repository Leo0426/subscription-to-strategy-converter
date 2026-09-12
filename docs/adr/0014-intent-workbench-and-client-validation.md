# Service intent workbench and explicit client validation

Status: accepted

The operator requested a 5.0 workflow that exposes client compatibility, predictable service egress, editing of stable subscriptions, template upgrades, and on-demand diagnosis. The single-page layout of ADR 0010 and the no-history boundary of ADR 0012 remain.

## Decision

The standard workbench saves ServiceRoute preferences instead of copied rule graphs. A shared, packaged service catalog supplies RulePacks and current service overrides; the published Leo template keeps its standalone rules, with catalog-owned inline rules synchronized and checked. Legacy SelectedPolicy and Claude transforms remain compatible. This supersedes ADR 0009's copied PolicySnapshot as the default for new workbench Profiles, and generalizes ADR 0004's service boundary for explicit modern routes without changing legacy Claude requests.

Fixed routes accept exactly one current node (or explicit DIRECT/REJECT); manual routes may delegate to a visible policy group; failover requires an explicit primary and backup node and uses a generic connectivity probe. Probe health never promises service acceptance. Missing fixed nodes fail clearly instead of silently choosing another route.

New workbench requests declare publication targets. Checking and saving compile each selected target; hard errors block saving and compatibility warnings are returned for review. Configuration validity, rule simulation, client observation, HTTP reachability, and full browser login are distinct claims. Last-successful artifacts retain their existing external-failure semantics.

The existing Profile ID/token remain stable on edit. Legacy policy graphs are preserved until the user explicitly previews and applies an upgrade; an upgrade shows policy changes and retained/discarded preferences. No historical revision store is added.

Runtime diagnostics use only operator-configured Mihomo controller endpoints or the locally installed Surge CLI. Browser requests cannot supply arbitrary controller URLs, credentials, shell commands, or target URLs. Runtime actions are read/probe-only, credentials never enter Profile data, and no client choice is mutated. Container installations without a configured controller show runtime verification as unavailable.

## Trade-offs

This adds a small validation/diagnosis interface but avoids a full remote-client control panel. Existing clients can still fetch stable subscriptions without adopting new UI concepts. A shared catalog and synchronization check are preferable to silently diverging handwritten rule copies. Legacy snapshots require an explicit upgrade because inferring arbitrary custom policy intent would lose user changes.
