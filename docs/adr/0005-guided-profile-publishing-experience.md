# Guided Profile publishing experience

Status: accepted

Subflow's current split between a simple converter and a dense policy workspace gives equal visual weight to creation, debugging, compilation, system status, and persistence. We will replace that split with one task-oriented product experience: existing users land on Profile management, first-time users enter a four-step guided publishing flow, and expert workspace capabilities remain available through a contextual inspector. This supersedes ADR 0001's workspace-first product presentation while retaining `PolicyWorkspace` as the internal analysis and compilation model.

## Considered Options

Guided Profile publishing with progressive disclosure:
- Benefits: aligns the interface with the durable user outcome, keeps the common path short, and preserves expert controls without making them prerequisites.
- Costs: requires a coordinated information-architecture rewrite and clear state transitions between steps.

Separate simple and advanced pages:
- Benefits: smaller short-term change and a minimal entry point for new users.
- Costs: duplicates concepts and state, forces users to switch mental models, and lets the advanced page continue accumulating unrelated panels.

Workspace dashboard with equal-status tools:
- Benefits: exposes all engineering capabilities immediately.
- Costs: makes users understand templates, providers, graph analysis, compilation, and persistence before completing the primary task.

## Decision

The primary flow is:

```text
Import subscription
  -> choose clients and accept or replace recommended templates
  -> customize service routing
  -> validate and publish Profile
```

- Users express the desired client and routing outcome before choosing template files; the system recommends an auditable compatible template combination.
- Publishing a token-protected Profile is the primary completion action. YAML preview, temporary links, and file downloads are secondary expert actions.
- Nodes, findings, simulation, policy editing, graph data, and source configuration live in one contextual inspector instead of peer-level workspace tabs.
- When Profiles exist, the landing view manages them. When none exist, the same page enters the creation flow automatically.
- Creating and editing a Profile reuse the same flow and state model.

## Consequences

- Normal system health does not occupy primary page space; failures appear as actionable global notices.
- Validation runs as part of publishing and opens the inspector to the relevant finding when blocked.
- Profile cards expose status and common actions; destructive and diagnostic actions live in a secondary menu.
- `PolicyWorkspace` remains the deep internal module for analysis, simulation, visualization, and compilation, but it is no longer the product's top-level navigation model.
- Future service strategies such as OpenAI or Gemini extend the routing step rather than adding new top-level panels.
