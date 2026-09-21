# AnyTLS compatibility

Subflow accepts AnyTLS nodes from Mihomo YAML and supported Surge `[Proxy]` entries. They can participate in Leo's node pools, fixed ServiceRoutes, and Profile subscriptions for Mihomo, Surge and paired Shadowrocket outputs. The protocol does not select a region or establish whether an AI service accepts an egress IP.

## Fields

| Mihomo | Surge | Handling |
| --- | --- | --- |
| `password` | `password` | Required; quoted commas, whitespace, quotes and backslashes preserved |
| implicit TLS | implicit TLS | Always enabled in the shared node model |
| `sni` | `sni` | Hostname preserved; Surge `sni=off` import is rejected because disabling SNI has no equivalent mapping here |
| `skip-cert-verify` | `skip-cert-verify` | Preserved; verification remains enabled unless the source explicitly disables it |
| `alpn` | `alpn` | Ordered list ↔ quoted comma-separated value |
| `fingerprint` | `server-cert-fingerprint-sha256` | SHA-256 certificate fingerprint; colon separators removed for Surge, original retained for Mihomo |
| `name-cert-verify` | `server-cert-verify-name` | Certificate verification hostname preserved independently of SNI |
| `disable-reuse` | inverse of `reuse` | Explicit false and true preserved; no arbitrary idle-timeout substitution |
| `udp` | automatic UDP-over-TCP | Surge always enables its relay; `udp: false` produces a compatibility warning |

Mihomo's `client-fingerprint` is a TLS client fingerprint and is separate from certificate pinning. Surge does not offer an equivalent mapping here. Explicit client fingerprint, client metadata and the three idle-session tuning options produce `ignored_node_options` warnings on Surge output; they remain unchanged in Mihomo output. Missing tuning fields are not filled with invented defaults.

Unknown or unmapped options, including ECH, Reality, ShadowTLS/Restls/JLS wrappers, mTLS certificate/private key and proxy chains, cause the affected Surge node to be skipped with `unsupported_node_options`. Diagnostics contain field names, never their values. Other nodes with the same protocol remain available. Emptied groups are removed, direct rules targeting them become REJECT, and a fixed ServiceRoute selecting such a node fails client validation/publication. Surge-source entries with options outside the supported import mapping fail explicitly instead of losing settings; request the provider's Mihomo subscription for those nodes.

## Client versions

| Emitted Surge options | Minimum iOS | Minimum Mac |
| --- | --- | --- |
| Basic AnyTLS v2 | 5.17.0 | 6.4.3 |
| ALPN | 5.20.0 | 6.7.0 |
| Separate certificate verification hostname | 5.21.0 | 6.8.0 |

The exporter returns a `client_version_requirement` warning based on the nodes actually emitted. This is separate from the existing Leo policy compatibility baseline and does not discover the installed client version. Mihomo import of `reuse=false` requires a core that supports `disable-reuse`; it is checked against the official v1.19.31 release. Older cores are not claimed to preserve that option. Shadowrocket now receives the airport's original native subscription without reserializing its AnyTLS options (ADR 0016); device behavior still requires client QA. Experimental sing-box output is outside this support scope.

## Verification

Automated tests cover field preservation through workspace serialization, pre-upgrade workspace authentication, quoted credentials, skipped-node closure, client validation and Profile publication from pure AnyTLS subscriptions, including fixed Claude and OpenAI routes. Synthetic configuration files pass Surge's native `--check` and Mihomo v1.19.31's `-t`. These checks validate configuration syntax and routing structure, not a handshake with a live AnyTLS server, UDP delivery or AI login.

Official references checked 2026-09-21: [Surge AnyTLS](https://manual.nssurge.com/policies/anytls.html), [Surge TLS](https://manual.nssurge.com/policies/tls.html), [Mihomo AnyTLS](https://wiki.metacubex.one/config/proxies/anytls/), [Mihomo v1.19.31 implementation](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/outbound/anytls.go).
