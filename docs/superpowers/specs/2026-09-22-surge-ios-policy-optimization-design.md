# Surge iOS policy optimization

## Context

Subflow 6.2 preserves a native Surge subscription's connectivity envelope and replaces only routing. On the operator's Surge iOS 5.22.1 profile, that produces a structurally valid artifact with 144 nodes, but the two generated latency groups probe Shadowsocks nodes that are known to fail in the operator's NR environment. The same artifact also emits three `PROCESS-NAME` rules even though Surge iOS ignores that Mac-only rule type. Source-owned `[General]` settings and insecure TLS flags are preserved correctly but receive no targeted security review.

This change keeps ADR 0016's source-ownership boundary. It changes generated routing membership and target validation, but it does not silently rewrite source-owned connectivity settings.

## Goals

1. Let a Profile explicitly restrict Surge automatic latency groups by node protocol while keeping every compatible node in manual selectors.
2. Treat the existing `surge` target as the documented Surge iOS compatibility target and stop emitting Mac-only `PROCESS-NAME` rules.
3. Report risky or obsolete native Surge settings without changing their source text.
4. Use Surge's documented 100 ms automatic-group tolerance in the canonical Leo policy.
5. Preserve old Profiles and all non-Surge targets unless the new preference is explicitly enabled.

## Non-goals

- Do not infer NR behavior from the `ss` protocol or silently make AnyTLS-only probing the global default.
- Do not add a broad automatic AI egress group. Existing explicit two-node `ServiceRoute` fallback remains the supported mechanism.
- Do not add or republish application domain lists in this change. The three process rules remain available to Mihomo; Surge iOS drops them with a compatibility warning.
- Do not rewrite `allow-wifi-access`, `doh-server`, logging, IPv6, tunnel-scope, or certificate settings in a native source.
- Do not test live proxy reachability, AI acceptance, or browser login.

## Product interface

Add an optional `surge_preferences` object to `ConvertRequest` and persisted Profile requests:

```json
{
  "surge_preferences": {
    "auto_test_protocols": ["anytls"]
  }
}
```

`auto_test_protocols` is normalized to distinct lower-case protocol names. An absent object or empty list means no filtering and preserves existing behavior. The preference applies only while producing or checking the `surge` target.

The standard workbench exposes one compact selector when Surge is among the publication targets:

- `全部兼容节点` produces an empty protocol list.
- `仅 AnyTLS` produces `["anytls"]`.

Opening, editing, upgrading, saving, and refreshing a Profile must retain the preference. Legacy Profile JSON without the field remains valid.

## Build and rendering flow

Introduce a target-specific automatic-group filter after the Leo template has materialized dynamic membership and before target compilation. Its interface accepts the current config, complete `ProxyNode` inventory, and allowed protocol set, then returns:

- the closed policy graph after filtering;
- one diagnostic per affected `url-test` group containing its name, member count before filtering, member count after filtering, and allowed protocols.

Only node members of `url-test` groups are filtered. Nested policy-group members are retained. `select`, `fallback`, and `load-balance` groups are not filtered, so `手动选择` retains all compatible nodes and explicit AI primary/backup groups remain unchanged.

If filtering empties a group, existing graph-closure behavior removes the empty group and stale parent references. Publication remains allowed when another valid route remains, and the diagnostic explains the removal. Missing rule targets continue to follow the existing compatibility fallback logic.

The build pipeline returns a named result containing nodes, config, source context, and policy diagnostics. Target compilation appends its compatibility warnings. Multi-target `/check` builds the Surge target with Surge preferences rather than reusing a Mihomo-shaped config.

## Surge iOS rule capabilities

Define one shared Surge iOS rule-type set used by both template capability analysis and the compiler. `PROCESS-NAME` is excluded. The compiler drops all such rules and returns an `unsupported_rule_types` warning with:

- unique unsupported type names;
- the number of unique types;
- the total number of skipped rules.

The Leo source keeps the three process rules for Mihomo. A generated Surge iOS artifact contains none of them and still ends with exactly one `FINAL` rule.

This change deliberately defines the current `surge` target as iOS. Future Surge Mac support requires an explicit client capability/profile rather than weakening the iOS contract.

## Native Surge audit

Add a read-only audit beside native Surge profile handling. It parses option names case-insensitively and never includes option values, resolver URLs, credentials, or subscription tokens in diagnostics.

For a native source, emit non-blocking warnings for:

1. `allow-wifi-access=true` without `wifi-access-http-auth`;
2. legacy `doh-server` usage;
3. `loglevel=info`;
4. `include-all-networks=true` together with enabled `include-apns` and/or `include-cellular-services`;
5. emitted TLS nodes with certificate verification disabled, aggregated as a count without credentials.

These warnings are visible through `/check` and existing compile-warning headers. `replace_surge_routing()` must continue to preserve every audited source line byte-for-byte except the already-removed managed-update directive and replaced routing sections.

Cross-format Surge output retains its safe generated defaults (`loglevel=notify`, LAN access absent/default false). TLS verification warnings apply to both native and cross-format output because the risk belongs to the emitted node.

## Canonical policy change

Set both `自动选择` and `香港自动` to an explicit `tolerance: 100`. Keep their current probe URL, interval, lazy behavior, timeout metadata, and failure threshold. AI selectors and explicit service fallback groups do not gain latency-based switching.

## Error handling and compatibility

- Unknown protocol strings are accepted as future-compatible selector values; they simply match no current nodes and produce an empty-group diagnostic when applicable.
- The protocol preference does not remove nodes from `[Proxy]`, source auxiliary groups, or manual selectors.
- Audit findings never block publication.
- Target compile errors keep their existing blocking behavior.
- Warning payloads remain redacted and JSON serializable.
- Existing saved artifacts are invalidated only when a Profile is edited through the existing generation mechanism; reading an old Profile does not mutate it.

## Test strategy

Use test-driven changes with these regression fixtures:

1. A 144-node inventory containing 83 Shadowsocks and 61 AnyTLS nodes, with 15 Hong Kong Shadowsocks and 16 Hong Kong AnyTLS nodes. With the Surge preference enabled, assert `自动选择=61`, `香港自动=16`, and `手动选择=144`. Without it, assert the existing counts remain unchanged.
2. Assert Mihomo output is unchanged when the Surge preference is present.
3. Assert workbench payload, Profile create/edit/draft/upgrade, and refresh retain the preference.
4. Assert Surge iOS output drops all three `PROCESS-NAME` rules, reports one unsupported type and three skipped rules, and retains one terminal `FINAL`.
5. Assert the shared capability set cannot drift between service-template analysis and compilation.
6. Assert native source audit warnings for each listed condition, verify no secret option values appear, and verify the original non-routing sections remain unchanged.
7. Assert both canonical automatic groups use tolerance 100 and explicit service fallback semantics remain unchanged.
8. Run the full Python suite plus the existing frontend syntax/version checks used by the repository.

## Acceptance criteria

- The operator can save a Surge Profile whose automatic groups contain only AnyTLS while manual selection retains all nodes.
- Surge iOS artifacts contain no `PROCESS-NAME` rules and surface the loss explicitly.
- Risky native settings are visible as non-blocking, redacted warnings and are not rewritten.
- Existing Profiles without `surge_preferences` render exactly as before except for the intentional tolerance and iOS rule-capability corrections.
- Mihomo and Shadowrocket behavior remains unchanged.
- All repository verification gates pass from a clean isolated worktree.

## Architectural decisions

- **Locality:** automatic membership filtering lives behind one target-policy operation rather than being duplicated in the template, UI, and Surge serializer.
- **Source ownership:** native connectivity remains source-owned; Subflow audits but does not silently repair it.
- **Explicit intent:** NR-specific protocol eligibility is persisted user intent, not a heuristic inferred from protocol names or failed probes.
- **Stable AI egress:** this change reuses explicit two-node service fallback and does not allow generic latency tests to authorize AI egress changes.
