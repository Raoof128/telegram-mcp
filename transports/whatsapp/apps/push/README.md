# Push / notification subsystem — STRUCTURED, Phase-0/Apple-gated (not built in CI)

Recorded contract (ledger #48). Notifications are **content-free** (approval requests,
DLQ/circuit-breaker alerts, retention warnings) — the payload never carries message text.

- Provider is a named actor: **APNs** (paid Apple membership) or **Pushover/ntfy** (each a
  privacy/dependency actor even with content-free payloads). Chosen at Phase 0.
- Enforce per-category **rate limits** (a token bucket) so a noisy failure mode cannot spam.
- No message content, contact identity, or body ever leaves in a push; the phone fetches
  the device-sealed draft detail over the Tunnel (Phase 4 relay) after the user taps.
