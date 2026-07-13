# ADR 0001: Workspace-first Mihomo MVP

## Status

Superseded by ADR 0005

## Context

The project started as a subscription-to-strategy converter: fetch an authorized proxy subscription, normalize it through subconverter, apply a template, and return client-ready configuration.

The codebase has grown beyond raw conversion. It now has a `PolicyWorkspace` IR, policy graph builder, analyzer, simulator, community template catalog, and multiple compiler paths. The product narrative must therefore stop treating YAML generation as the center of the system.

The main risk is scope drift: continuing to add target platforms, template variants, and conversion toggles before the policy workspace experience is coherent.

## Decision

The next MVP is a Workspace-first Mihomo MVP.

The product core is `PolicyWorkspace`: a structured workspace containing proxy nodes, proxy groups, rules, rule providers, settings, analyzer findings, graph data, simulator traces, and compile output.

Mihomo is the only quality-bar compiler for this MVP. Surge and sing-box remain useful experimental compilers, but they are not the primary product promise until they pass the same workspace, analyzer, simulator, and golden-output expectations.

The main user flow is:

```text
Subscription + Template
  -> Policy Workspace
  -> Analyze
  -> Simulate
  -> Visualize
  -> Compile Mihomo YAML
```

The `/subscribe` conversion URL remains supported, but it is no longer the conceptual center of the product.

## Consequences

- UI and documentation should present Subflow as a policy workspace builder, not a simple subscription converter.
- New work should improve the workspace loop before expanding platform breadth.
- Analyzer findings and simulator traces are first-class product outputs, not only debug helpers.
- Mihomo output should receive golden tests and compatibility hardening first.
- Surge and sing-box should be labelled experimental in product surfaces until their semantics are explicitly verified.

## Alternatives Considered

### Continue as a subscription conversion tool

This is simpler in the short term, but it competes directly with existing conversion tools and does not use the policy IR, analyzer, graph, and simulator as durable assets.

### Build a full multi-platform control plane immediately

This matches the long-term vision, but it increases implementation surface before the workspace model is stable. It would make failures harder to diagnose because platform semantics, UX, and IR design would all be moving at once.

## Follow-up Work

- Make the homepage workspace-first: create workspace, inspect findings, simulate traffic, view graph, compile Mihomo.
- Add Mihomo golden-output tests for representative workspaces.
- Move experimental platform language into UI and README.
- Consider a later ADR for persistence/versioning once in-memory workspace workflows are stable.
