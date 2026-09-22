# Telegram MCP Phase 2 Design — On-Demand Privileged Runtime, Identity, Consent, Authority

**Status:** Revised for the on-demand requirement plus gauntlet fixes; pending re-review before the implementation plan.
**Date:** 2026-09-22 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`)
**Roadmap:** `docs/superpowers/plans/2026-09-22-telegram-mcp-release-roadmap.md` (Phase 2)
**Basis:** Phase-1 synthetic foundation, `main` at `0d07ccb`, 168 tests green.
**Approach:** B — On-Demand Privileged Runtime (privileged service account, session-scoped process).

## Requirement (new, controlling)

No always-on daemon. The operator runs `telegram-mcp start`, uses the
capability, then runs `telegram-mcp stop`. While OFF, nothing listens: no MCP
ports, no admin socket, no consent rendezvous, no tunnel-client, no Telegram
connection, no runtime process. Privilege separation is preserved without
24/7 operation: a privileged service account is not a permanently running
service.

## Environment facts (confirmed)

Full Phase 2 including the native consent path. Admin rights available: service
users are created for real. Touch ID present: LocalAuthentication + Secure
Enclave keys. Apple Developer ID available: stable code identity for the signed
agent. Streamable HTTP is kept for Codex/Claude: a closed localhost port is the
hard off-switch, which fits the manual-start model better than client-spawned
stdio.

## 1. Lifecycle and architecture

States: `OFF → STARTING → READY → STOPPING (DRAINING) → OFF`.

The process is named `telegram-mcp-runtime` (the word "daemon" is retired from
this design; the service account stays `telegram-mcpd` to avoid churn). One
Python runtime per start, running as the service account: MCP ingress on 8766
(bearer leases) and 8767 (mTLS, only in ChatGPT mode), authority modules,
consent broker, admin Unix socket, and a fake Telegram adapter seam that Phase
4 fills with the real adapter. The interactive launcher (operator-owned) starts
and stops three independent launchd jobs — runtime as `telegram-mcpd`,
consent agent in the GUI session, tunnel as `telegram-mcp-tunnel` — escalating
through interactive sudo per job; the runtime never spawns or owns its
siblings. Start modes: `start --local` (runtime + consent UI, 8766 only, for
Codex/Claude), `start --chatgpt` (adds tunnel-client, 8767 only), `start --all`
(both listeners).

Startup readiness ordering (normative; no listener before step 15, no tunnel
polling before READY):

```text
1. generate runtime_id (128 random bits, memory only)
2. acquire single-runtime lock (second start fails closed; PID-bearing
lockfile with liveness check — a stale lock from a crashed runtime is
reclaimed, a live owner fails the start)
3. load secrets; 4. verify secret permissions
5. open SQLite; 6. foreign_keys=ON; 7. quick_check
8. run/verify migrations; 9. startup GC
10. load authority state; 11. verify security lock state
12. start consent channel; 13. consent-agent mutual-auth handshake
(pinned-key challenge-response at connect on `consent.sock`)
14. initialise fake/real Telegram adapter; 15. open MCP listeners
16. advertise READY; 17. optional tunnel-client starts
```

Shutdown: stop accepting requests (new sensitive calls during DRAINING are
rejected with `INTERNAL_ERROR` — fail-closed; the runtime is not locked, so
`SECURITY_LOCKED` would be wrong) → invalidate pending consent → stop tunnel
intake → cancel queued work → bounded 5-second grace for active calls, then
cancel the remainder and discard un-serialised payloads → close Telegram
(disconnect only; stopping MUST NOT call `log_out()`) → close SQLite → remove
Unix sockets → release process lock → stop consent UI. Then OFF.

`runtime_id` is bound into bearer leases, consent challenges, admin challenge
nonces, and cursors. Restart mints a new `runtime_id`,
annihilating every ephemeral capability without database mutation. (`tgl_`
handles are memory-only and die with the process, so they need no binding.)

## 2. Bootstrap control vs runtime admin IPC (normative split)

Two distinct control layers. Bootstrap control — `start`, `stop`, `status` —
works while OFF and never needs the runtime socket; `stop` while OFF is a
no-op success. Runtime admin IPC (scope,
project, policy, client, lock, auth, rotate, doctor) exists only when READY.

One-time install registers the privileged service definition (SMAppService /
launchd; exact API verified at implementation time) and requires one
interactive admin authentication; `start`/`stop` never do. `start` triggers the
approved service manager to launch the runtime as the service account. No shell
`sudo -u`, no passwordless sudo rule, no setuid wrapper. The installed helper
definition is persistent; the running process is not. Child processes
(tunnel-client, consent UI) are tracked in a runtime-owned PID file; at start,
a live PID with an exact cmdline-plus-UID match is terminated and started
fresh — deterministic clean slate, never silent adoption of a stranger.

Socket layout:

```text
/private/var/run/telegram-mcp/   owner telegram-mcpd:telegram-mcp-admin  0770
  consent.sock                    owner telegram-mcpd:telegram-mcp-admin  0660
  admin.sock                      owner telegram-mcpd:telegram-mcp-admin  0660
```

Filesystem permissions grant reachability; cryptographic identity grants trust.

## 3. Keys

Normative registry (owner, algorithm, persistence). Phase 2 provisions only the
rows it requires; future private keys are not generated early:

| Key                  | Algorithm    | Owner           | Persistent? | Phase-2 use              |
| -------------------- | ------------ | --------------- | ----------: | ------------------------ |
| principal-key        | HMAC-SHA-256 | runtime account |         yes | principal pseudonym      |
| cursor-key           | HMAC-SHA-256 | runtime account |         yes | cursor query binding     |
| privacy-key          | HMAC-SHA-256 | runtime account |         yes | privacy-safe digests     |
| challenge-key        | Ed25519      | runtime account |         yes | sign consent challenges  |
| lease-seed (per client) | HMAC-SHA-256 | runtime account |      yes | bearer lease MAC      |
| tunnel TLS/mTLS keys | X.509/SPKI   | respective service accounts | yes | ingress 8767     |
| agent approval key   | P-256 Secure Enclave | operator | device-bound | Touch ID signatures |
| agent transport key  | Ed25519 software (operator keychain, no biometry) | operator | yes | rendezvous auth; starting MCP costs no biometric prompt (implementation row beyond the twelve spec purposes) |
| disclosure/audit/backup keys | Ed25519/HMAC | runtime account | NO (Phase 3) | registry only |

Daemon file-backed secrets are `0600` in the service account home (spec permits
this with `doctor` validation). Key IDs are recomputed on load
(`ed25519:`/`p256:`/`spki:sha256`).

Pairing: the operator generates the agent approval key in the Enclave and a
software transport keypair, storing the approval key's encrypted
`dataRepresentation` plus the transport private key in the login keychain
under the app access group; both publics go to the daemon for separate pin
slots. The daemon's challenge public key is stored in a
trusted pairing record in the operator's protected keychain — never compiled
into the agent bundle — so daemon-key rotation needs a re-pairing ceremony,
not a rebuild. Both directions are verified by on-screen fingerprint
comparison. Rotation is per-purpose with history retention; `doctor
--production` verifies separation, IDs, and permissions without printing
material.

## 4. Consent protocol

Signed challenge (canonical JCS) contains: `canonical_request_hmac`,
`display_digest`, `exposure_snapshot_digest` (synthetic-zero form in Phase 2:
`{"bytes_disclosed": 0, "mode": "synthetic", "records_disclosed": 0, "schema":
"tg-mcp-exposure-snapshot/v1"}` — Phase 3 swaps the computation, never the
field), principal, client, account, tool, both epochs,
`project_scope_digest`, `runtime_id`, nonce, expiry. The display payload
(client/action/project/peer display strings, risk class) is hashed as
`display_digest = SHA256("telegram-mcp-display-v1" || JCS(display_payload))`,
lowercase hex; the
agent recomputes it with a constant-time compare and renders only on equality.
This closes UI substitution: the approved bytes and the seen description
cannot diverge.

The `tgu_` handle is an in-memory lookup key only. Display text is
daemon-reconstructed (escaped, C0/C1 stripped, ≤160 codepoints). Flow: freeze
args → bind challenge → daemon-sign → agent verifies signature → recomputes
display digest → Touch ID prompt → agent signs exact bytes with the Enclave key
→ daemon verifies against the pinned key with exact-once consumption →
authority revalidation → fake adapter execution. The 45-second wait costs no
execution budget. Rate limits 5/min and 30/hr per client; no fan-out.
Challenges invalidate on cancel/disconnect/disable and on any epoch, grant, or
snapshot change. A shell may trigger a prompt but can never approve one.

Phase-2 flow ends at consent-verified. Budget reservation belongs to Phase 3:
Phase 2 defines a `DisclosureGate` protocol with a `SyntheticDisclosureGate`
that performs no accounting; Phase 3 replaces it with the exposure-budget,
receipt, and audit-chain services.

The consent UI is session-scoped: its per-user LaunchAgent definition is
installed once (disabled by default), loaded at `start`, and unloaded at
`stop`; the agent additionally auto-exits after runtime disconnect plus a
short grace. Nothing Telegram-related remains active after stop.

## 5. Authority

One central scope-policy service called by every tool; no per-tool
implementations. Effective access = owner allowlist ∩ project membership ∩
client grant, evaluated before retrieval and again before serialisation. Owner
policy: allowlist default, archived chats excluded, deny wins, canonical
`(account, peer-type, peer-id)` identity, `tgl_` snapshot handles for discovery
(5-minute, memory-only). Projects: explicit refs, enabled
checks, `can_read` plus `can_cross_search` grants, most-restrictive egress wins
for shared peers, no implicit all-projects mode.

Epochs: `policy_epoch` per (principal, account), `project_epoch` per project,
global `security_epoch`. Grant/egress changes break `project_scope_digest` even
without an epoch bump. Lock increments the security epoch, kills
consent/cursors/leases, returns `SECURITY_LOCKED` for sensitive calls; unlock
needs presence and starts a new epoch.

Refs: all ten prefixes, 130-bit CSPRNG, SQLite-mapped, re-authorised on every
use, non-enumerating `REF_NOT_FOUND`/`NOT_ACCESSIBLE`. Cursors: keyed-HMAC
query digests (never plain SHA-256 of text), 15-minute TTL, `runtime_id`-bound
(restart invalidates — a fail-closed extension beyond §23, stated here), GC at
startup plus opportunistically at most hourly while running plus best-effort
at clean shutdown. Seven invalidation triggers
map to `INVALID_CURSOR`, `CURSOR_POLICY_CHANGED`, `CURSOR_PROJECT_CHANGED`,
`CURSOR_EXPIRED`.

Ruling (stale-policy codes): cursor context returns `CURSOR_POLICY_CHANGED`;
`POLICY_CHANGED` is reserved for live-request revalidation.

## 6. Storage

One metadata SQLite database owned by the service account (directory `0700`,
files `0600`): all 19 tables, transactional migrations with `schema_version`.
At startup, before READY: integrity (`quick_check`) → migrations → GC →
exclusive session lock. (Two different locks: the §1 single-runtime process
lock is enforced in Phase 2; this session-file lock protocol is defined here
and enforced when the real adapter lands in Phase 4.)
`PRAGMA foreign_keys=ON` verified per connection.

Excerpt-width integrity rule (roadmap C2): the excerpt branch gains `IS NOT
NULL` with the 64–4000 range preserved. Shared-membership integrity rule
(roadmap C4): adding a peer already in another retained project is rejected
unless the operator marks the membership `shared`; enabling a project rejects
undeclared overlaps; all retained memberships are inspected, not just enabled
ones. Cross-table integrity (§12.3) is enforced by triggers plus contract tests
proving inconsistent direct writes fail. Required indexes per §12.4.

Ruling (default-deny): no access means row absence. Migrations and code MUST
NOT create default `client_projects` rows; `can_read DEFAULT 1` applies only to
explicitly created grants. The `settings` table sits behind a code-defined
closed settings registry; `tgl_` handles never touch SQLite.

## 7. Testing

Every claim gets a failing-first test. Real Touch ID signatures end to end.
Tamper, replay, cancellation, timeout, and rate-limit probes. Display-digest
mismatch (tampered UI payload) must refuse to render. Non-interactive
impersonation (`sudo -n -u` must fail for both service accounts; direct
secret-file reads denied). Deny-precedence matrix. Shared-membership
accept/reject rules. Client attribution fields on the audit-event model and
emission seam (chain and checkpoints stay Phase 3). Epoch invalidation for all
three epochs plus grant-change-without-epoch-bump.

On-demand runtime matrix: cold start, double start, stale-socket start,
post-crash start, stop while idle / during RPC / with consent pending /
with consent approved-but-unconsumed, restart inside a 60-second bearer
lifetime, restart with old cursor / `tgl_` / `tgu_`, tunnel orphan detection,
consent-UI orphan detection, SIGTERM, SIGKILL recovery, sleep/wake, network
loss during shutdown, DB busy during shutdown, lock contention. Core
assertion — when OFF: no MCP ports, no admin socket, no consent socket, no
tunnel-client, no Telegram connection, no runtime process.

Authority is tested headless against fake adapters. The Swift agent is tested
through its wire protocol against a stub broker. Service-account, Touch ID,
and install/start tests are marked platform-gated (macOS + hardware + admin).
Exit evidence maps 1:1 to the roadmap Phase-2 list, feeding gates E, H, M, N,
P and the privilege/key parts of R. SMAppService/launchd API specifics are
verified against current Apple documentation at implementation time.

## Out of scope (later phases)

Disclosure budgets/proofs/audit chain (Phase 3), the real Telegram adapter and
tool slices (Phase 4), retention/recovery/backup-restore (Phase 5), installed
clients and tunnel acceptance (Phase 6), release gauntlet (Phase 7). No
production deployment occurs as part of this design.

## Normative grounding

The frozen V0.1.10 specification controls whenever this document is silent
(spec SHA-256 above). All sections and appendices it marks normative are
controlling; appendices it marks historical, revision, or closure records are
informative only. Ambiguities resolved here: challenge-field lists,
display-digest addition, stale-policy error codes, default-deny row absence,
socket owner-only vs group wording.
