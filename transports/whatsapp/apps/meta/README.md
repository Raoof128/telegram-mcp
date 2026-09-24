# whatsvault-meta daemon — STRUCTURED, Phase-0/credential-gated (not built in CI)

The sole Meta-credential holder (ledger #43). It is a **process boundary**, not a
library: MCP / ingest / scheduler hold no token.

- `apps/meta/daemon.py` loads the `whatsapp_business_messaging` token from the Keychain
  and listens on a **Unix-domain socket** with restrictive perms (0700 dir, 0600 socket).
- Exposes exactly: `execute_approved_write(signed_envelope)` (self-authenticating; runs
  `whatsvault.approval.sender.execute_write`), `materialise_media(attachment_id)` (caller
  restricted to ingest), `health()`. No arbitrary Graph POST / arbitrary URL GET.
- Pins `META_GRAPH_VERSION` (Phase 0 finding, ledger #44); HTTP auto-retries disabled.
