# Persistent profiles with tokenized subscriptions and stale fallback

Status: accepted

Subflow needs subscription URLs that survive process restarts without exposing the original subscription URL. We will persist Profiles and their last successful compile artifact in SQLite, address them with a profile ID plus an independently rotatable token, and store only the token hash. The original subscription URL remains plaintext inside the locally protected database for the first version because introducing application-level key management would add recovery and deployment complexity without removing the runtime secret requirement.

## Considered Options

Profile ID plus independent token:
- Benefits: separates lookup from authorization and permits token rotation.
- Costs: clients must retain two opaque values.

Unguessable profile ID only:
- Benefits: smaller interface.
- Costs: a leaked ID cannot be rotated independently.

Application-level encryption for the subscription URL:
- Benefits: protects database contents when the encryption key is stored separately.
- Costs: adds key provisioning, rotation, backup, and loss-recovery requirements to the personal deployment MVP.

## Consequences

- The SQLite database is a sensitive deployment artifact and must be protected with host filesystem permissions and backups.
- Stale cached output is returned only when an external subscription, subconverter, or remote-template dependency fails. Fresh artifact reuse is separately bounded as clarified below.
- Stale responses are marked with `X-Subflow-Stale: true`; authentication, invalid Profile data, and internal compile failures never fall back silently.

## 2026-09-21 clarification: fresh reuse and edit consistency

The authorized P0–P2 stability work extends the original failure-only cache with short-lived fresh artifact reuse (30 seconds by default). Fresh hits require the same saved Profile generation, target and current policy/compiler identity, and still authenticate every request. Force refresh bypasses fresh reuse. This changes the original restriction on all cached output; the restriction remains in force for stale fallback.

Saving a Profile increments its generation and invalidates every target artifact. Atomic generation-checked writes prevent earlier in-flight requests from repopulating the cache after an edit. During an external failure, a same-generation, same-target artifact may carry an older policy/compiler identity, but it retains its actual identity and generation time and is explicitly marked stale. This introduces no historical Release/ProfileRevision or rollback system (ADR 0012). Behavior, migration and verification are documented in [Subscription refresh](../subscription-refresh.md).
