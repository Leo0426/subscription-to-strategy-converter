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
- Cached output is returned only when an external subscription, subconverter, or remote-template dependency fails.
- Stale responses are marked with `X-Subflow-Stale: true`; authentication, invalid Profile data, and internal compile failures never fall back silently.
