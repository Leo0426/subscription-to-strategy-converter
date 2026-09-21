# Airport subscriptions own connectivity across public clients

Status: accepted

The operator reported that an original Surge subscription worked while the generated subscription failed, and explicitly required the same preservation policy for Mihomo and Shadowrocket. Source and generated profiles differed in DNS, host mappings and probes because the previous pipeline extracted nodes and rebuilt a full template configuration. That is an unnecessary expansion of Subflow's policy responsibility; these differences alone do not establish the device failure's root cause.

## Decision

The airport source owns connection parameters and common client settings. Subflow owns ordered routing rules and the groups/providers required to express them. This refines ADR 0006 and supersedes the earlier interpretation of the canonical base in ADR 0007 that allowed Leo DNS/TUN settings to overwrite subscription settings. ADR 0013's paired links remain; its reconstructed Shadowrocket node format is superseded by native passthrough.

- Same-format Surge publication replaces routing within the native profile, retaining non-routing sections and auxiliary groups.
- Same-format Mihomo publication merges compiled routing into the source YAML envelope, preserving common fields, raw nodes, original node names and source group/provider dependencies. Generated name collisions are disambiguated without rewiring native references.
- Shadowrocket negotiates its own native source. Its subscription text is returned unchanged, including URI/Base64 encoding and provider metadata. A complete native INI retains all non-routing sections and auxiliary groups while routing is replaced; a nodes-only source gets only a groups/rules companion. There is no Mihomo-to-Shadowrocket common-settings reconstruction.
- Native Shadowrocket parsing is inventory-only: names, protocol, endpoint and opaque source fingerprints. It never produces connection data for another client's serializer. Ambiguous names or malformed inventory fail clearly instead of silently rewriting or dropping nodes.
- Missing settings remain missing. Native Mihomo nodes do not receive cross-format repairs; these stay at the actual conversion boundary. Selecting rule mode when required for Mihomo routing is reported.
- Target-specific fetch identities are part of subscription single-flight and publication revision keys. Native Shadowrocket publication metadata travels in headers, so adding a comment cannot corrupt its original Base64 body.
- The source remains separate from policy IR. Parsing a source into an inventory does not authorize regenerating its entire connection configuration.

## Validation boundary

API tests cover render, direct subscriptions, saved publications and source refresh. Synthetic fixtures cover source dependencies, generated-name collisions, absence of defaults and redacted warnings. Private operator subscriptions are used only for local structural comparisons and are not committed. Parser/CLI validation does not establish iOS import or live network connectivity; device verification remains distinct.
