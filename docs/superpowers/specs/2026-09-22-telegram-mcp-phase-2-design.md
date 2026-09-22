# Telegram MCP Phase 2 Design — Privileged Runtime, Identity, Consent, Authority

**Status:** Approved section-by-section; pending written-spec review before the implementation plan.
**Date:** 2026-09-22 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`)
**Roadmap:** `docs/superpowers/plans/2026-09-22-telegram-mcp-release-roadmap.md` (Phase 2)
**Basis:** Phase-1 synthetic foundation, `main` at `8f28b10`, 168 tests green.
**Approach:** A — single Python daemon + minimal Swift consent agent, file-backed daemon secrets.

## Environment facts (confirmed)

Full Phase 2 including the native consent path. Admin rights available: service
users are created for real. Touch ID present: LocalAuthentication + Secure
Enclave keys. Apple Developer ID available: stable keychain identity for the
signed agent and LaunchAgent.

## 1. Architecture

Three OS users: `telegram-mcpd` (daemon, session files, SQLite, policy, daemon
keys), `telegram-mcp-tunnel` (tunnel-client binary, mTLS key,
`CONTROL_PLANE_API_KEY` only), and the interactive operator (coding clients
plus the Swift consent agent in the GUI session via LaunchAgent).

One Python `telegram-mcpd`: MCP ingress on 8766 (bearer leases) and 8767
(mTLS), authority modules, consent broker, admin Unix socket, and a fake
Telegram adapter seam that Phase 4 fills with the real adapter. `tunnel-client`
is unchanged, forwarding to 8767. The Swift agent is a small signed binary.

Channels: coding clients → 8766 loopback bearer leases; tunnel → 8767 mTLS
(`openai_tunnel` only); agent ↔ broker over a daemon-owned rendezvous socket
(group `telegram-mcp-admin`, mode 0770) with cryptographic mutual auth via
pinned keys — socket permissions are reachability only, signatures are the
trust. The admin CLI talks to the owner-only Unix socket with peer-credential
checks.

## 2. Keys

Twelve purposes per §9.6.1, each its own row. Principal/cursor/privacy-digest
HMAC seeds (≥256-bit CSPRNG, `0600` daemon-owned files; cursor key differs from
principal). Disclosure/audit-checkpoint/backup Ed25519 signers (`0600` files;
rotation starts a new key ID; public keys retained receipt-retention + 30-day
grace in `verification_keys`). Audit-chain HMAC. Daemon challenge Ed25519
(public pinned in the agent). Agent approval P-256 in the Secure Enclave,
non-exportable, Touch ID per signature. Per-client lease HMAC seeds. Tunnel TLS
and mTLS keys in their service-account homes. Backup X25519 recipient (stricter
age format); the daemon never holds the recovery private key.

Key IDs are recomputed on load (`ed25519:`/`p256:`/`spki:sha256` + 64 lowercase
hex). Pairing ceremony: the operator generates the agent key in the Enclave and
exports the public key for the daemon to pin; the daemon exports its challenge
public key for the agent bundle to pin; both directions are verified by
on-screen fingerprint comparison. Rotation is per-purpose with history
retention; `doctor --production` verifies separation, IDs, and permissions
without printing material.

## 3. Consent protocol

Ruling (spec §9.4.1 vs §9.8 field lists): the §9.8 set — principal, client,
account, tool, canonical request HMAC, both epochs, both digests, nonce,
expiry — is the canonical signed JCS object for MCP challenges. The §9.4.1
extras describe admin-plane challenges and the display path. The `tgu_` handle
is an in-memory lookup key only. The display summary is daemon-reconstructed
(escaped, C0/C1 stripped, ≤160 codepoints, names client and action) and is
never signed.

Flow: freeze args → bind challenge → daemon-sign → agent verifies signature →
Touch ID prompt → agent signs the exact bytes with the Enclave key → daemon
verifies against the pinned key with exact-once consumption → budget reservation
→ 15-second execution. The 45-second wait costs no execution budget. Rate limits
5/min and 30/hr per client; no fan-out. Challenges invalidate on
cancel/disconnect/disable and on any epoch, grant, or snapshot change. A shell
may trigger a prompt but can never approve one. A CLI approve fallback exists
only behind the same OS presence check.

## 4. Authority

One central scope-policy service called by every tool; no per-tool
implementations. Effective access = owner allowlist ∩ project membership ∩
client grant, evaluated before retrieval and again before serialisation. Owner
policy: allowlist default, archived chats excluded, deny wins, canonical
`(account, peer-type, peer-id)` identity, `tgl_` snapshot handles for discovery
(5-minute, memory-only). Projects: explicit refs, enabled checks, `can_read`
plus `can_cross_search` grants, most-restrictive egress wins for shared peers,
no implicit all-projects mode.

Epochs: `policy_epoch` per (principal, account), `project_epoch` per project,
global `security_epoch`. Grant/egress changes break `project_scope_digest` even
without an epoch bump. Lock increments the security epoch, kills
consent/cursors/leases, returns `SECURITY_LOCKED` for sensitive calls; unlock
needs presence and starts a new epoch.

Refs: all ten prefixes, 130-bit CSPRNG, SQLite-mapped, re-authorised on every
use, non-enumerating `REF_NOT_FOUND`/`NOT_ACCESSIBLE`. Cursors: keyed-HMAC
query digests (never plain SHA-256 of text), 15-minute TTL, hourly GC, seven
invalidation triggers mapping to `INVALID_CURSOR`, `CURSOR_POLICY_CHANGED`,
`CURSOR_PROJECT_CHANGED`, `CURSOR_EXPIRED`.

Ruling (stale-policy codes): cursor context returns `CURSOR_POLICY_CHANGED`;
`POLICY_CHANGED` is reserved for live-request revalidation.

## 5. Storage

One metadata SQLite database owned by `telegram-mcpd` (directory `0700`, files
`0600`): all 19 tables, transactional migrations with `schema_version`,
`PRAGMA foreign_keys=ON` verified per connection, `quick_check` at startup
failing closed. C2 fix: the excerpt branch gains `IS NOT NULL` (64–4000 range
preserved). C4 rule: adding a peer already in another retained project is
rejected unless the operator marks the membership `shared`; enabling a project
rejects undeclared overlaps; all retained memberships are inspected, not just
enabled ones. Cross-table integrity (§12.3) is enforced by triggers plus
contract tests proving inconsistent direct writes fail. Required indexes per
§12.4; cursor/message-ref GC as specified.

Ruling (default-deny): no access means row absence. Migrations and code MUST
NOT create default `client_projects` rows; `can_read DEFAULT 1` applies only to
explicitly created grants. The `settings` table sits behind a compile-time typed
allowlist; `tgl_` handles never touch SQLite.

## 6. IPC, leases, identities

Admin socket `/var/run/telegram-mcp/admin.sock`: parent owned by
`telegram-mcpd`, group `telegram-mcp-admin`, mode `0660`, peer credentials
verified. The full §33 command surface (scope/auth/policy/lock/client/rotation)
is served here; a second Telethon session is never opened. Mutations
additionally require OS presence.

Ruling (owner-only vs group wording): the socket file is daemon-owned and
reachable only through the admin group — group readability is the
operator-access mechanism, not a second owner.

Leases: per-client ≥256-bit seeds, `tgml1` wire with audience, loopback, MAC,
and window checks, ≤60-second lifetime, ≤5-second skew, `sec` must equal the
current security epoch, independent per-client rotation. Helpers mint for
codex/claude clients only; the tunnel is mTLS-only and bearers are rejected on
8767. Tunnel identity: pinned SPKI mapped to `openai_tunnel`, bounded validity,
`rotate-binding` with history, Gate-M style verification. Service users are
created for real with non-login shells; non-interactive elevation probes
(`sudo -n -u` for both accounts) must fail; secrets are unreadable across
accounts; `doctor --production` enforces all of it.

## 7. Testing

Every claim gets a failing-first test. Real Touch ID signatures end to end
(challenge → prompt → Enclave sign → verify → consume). Tamper (modified
challenge bytes), replay (same signature twice), cancellation, timeout, and
rate-limit probes. Non-interactive impersonation (`sudo -n -u` must fail for
both service users; direct secret-file reads denied). Deny-precedence matrix
(deny beats allow across owner/project/client). Shared-membership accept/reject
rules. Client attribution in audit. Epoch invalidation for all three epochs
plus grant-change-without-epoch-bump.

Authority is tested headless against fake adapters. The Swift agent is tested
through its wire protocol against a stub broker. Service-account and Touch ID
tests are marked platform-gated (macOS + hardware) so CI intent stays clear.
Exit evidence maps 1:1 to the roadmap Phase-2 list, feeding gates E, H, M, N,
P and the privilege/key parts of R.

## Out of scope (later phases)

Disclosure budgets/proofs/audit chain (Phase 3), the real Telegram adapter and
tool slices (Phase 4), retention/recovery/backup-restore (Phase 5), installed
clients and tunnel acceptance (Phase 6), release gauntlet (Phase 7). No
production deployment occurs as part of this design.

## Ambiguities resolved here; spec body controls otherwise

Challenge-field lists (§9.4.1 vs §9.8), stale-policy error codes, default-deny
row absence, socket owner-only vs group wording — ruled above. All other
normative text in V0.1.10 §§1–50 and appendices K/L/N controls; appendices G/H
remain informative history.
