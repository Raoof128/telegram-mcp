# Comms spec v0.2 — owner-direct authority

**Status:** normative. Adopted 2026-09-24 (5b-3). This document is a clause-by-clause
delta. Where it is silent, the frozen Telegram specification
`telegram-mcp-v0.1.10-final-engineering-spec.md`
(SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`) and
WhatsVault's frozen design (`transports/whatsapp/docs/internal/specs/2026-08-27-whatsvault-design.md`)
still control.

**Design:** `docs/superpowers/specs/2026-09-24-comms-5b3-owner-direct-design.md` (rev 1),
with the owner's eight amendments and two gate additions.

## Controlling statement

There is **no Touch ID and no consent ceremony anywhere.**

- **An owner command is the authorization.**
- **The human confirmation lives in the conversation.** Claude drafts, shows the exact message and
  recipients, and asks "look, is it good?". On the owner's yes, Claude runs the operator command,
  and Claude Code's always-ask rule makes that single Allow the interlock.
- **The consent subsystem is deleted, not emulated.**
- **Historical evidence stays verifiable:** v1 receipts, v1 audit history and retained key
  material stay readable and verify exactly as before.

## Clause-by-clause delta

| Clause | v0.1.10 said | v0.2 says |
|---|---|---|
| §9.8 | The daemon is the authoritative consent boundary; every sensitive read needs an agent-signed approval of a daemon challenge. | **Retired.** No challenge is issued and none is consumed. The disclosure pipeline is: freeze → authority snapshot → estimate → consult and reserve → retrieve → revalidate → egress → commit (receipt, ledger, audit in one transaction) → anchor → handoff. Every barrier is unchanged: reservation/commit atomicity, the append guard, anchor-before-handoff, the degraded latch, revalidation before and after retrieval, and hard-threshold refusal before retrieval. |
| §9.8 frames, RV-1, pairing | `PROMPT` / `APPROVAL` / `DENIAL` frames over an RV-1 socket to a paired agent. | **Retired and tombstoned** (§Tombstones). There is no consent socket, no agent and no pairing. |
| §9.6.1 key registry | `challenge-key`, `agent-approval-key` and `agent-transport-key` are active purposes. | **Retired, not erased.** The rows stay in the registry with `retired = true`; nothing provisions or inventories them, and `doctor` reports them as retired, never as missing. Their names are never reused. |
| §11.1 ref prefixes | `tgu_` names a consent challenge. | **Retired.** `tgu_` no longer validates as a ref. |
| §23A / Appendix K | One receipt shape, `tg-mcp-disclosure/v1`, carrying `consent_verified`, `consent_key_id`, `consent_challenge_digest`. | **Two explicitly versioned shapes.** New receipts are v2 (below). Every v1 receipt is kept byte-identical and verifies exactly as before. Verification dispatches on the stored `proof_version`; nothing is inferred from nulls. |
| Appendix E.8 (`meta`) | `proof_payload` is the v1 shape. | The gateway releases **only** v2 payloads. The contract extractor applies one named overlay (`V0_2_META_OVERLAY` in `scripts/extract_contracts.py`) to E.8's `proof_payload` and nothing else; `--check` proves the file equals frozen extraction plus that overlay. |
| §23C.3 tiers | Below soft: normal prompt. At/above soft: elevated prompt. At/above hard: refuse before retrieval. | Below soft: proceed, `soft_threshold_exceeded = false`. At/above soft: proceed, `soft_threshold_exceeded = true`. At/above hard: **refuse before retrieval, unchanged.** The tier is taken from the ledger consult **immediately preceding reservation**, with no await between them. Same ledger state and same frozen worst-case estimate give the same tier as v0.1.10: it was interactive and is now informational. |
| §23C.3 binding | Reservation bound to `(client_id, security_epoch, project_scope_digest, consent_challenge_digest, request_nonce, expires_at)`; re-prompt on snapshot divergence. | The consent digest is replaced by the **call binding**: SHA-256 of `comms-call-binding/v1\0` followed by TG-JCS-v1 of `{args, nonce, tool}` over the validated arguments and a fresh per-call nonce. A commit must present the same binding. There is no re-prompt. |
| §23C.3 hard thresholds | Changed only through the admin plane "with user presence". | Changed only through the admin plane. |
| §33 admin plane | Mutations are separate user-presence-gated commands. | **Admin authority is the socket's peer credentials alone.** A `presence` argument is refused as malformed. `PRESENCE_REQUIRED` and CLI exit code 6 are retired. `consent status` and `consent approve` leave the command surface and answer `UNKNOWN_COMMAND` (§33 routes 49 commands). |
| §33 `pair` / start steps | The CLI pairs the agent; startup binds the consent socket and handshakes the agent (17 steps). | No `pair` verb. Startup has 15 steps. The launcher manages the runtime and tunnel jobs only. |
| §33.4 `doctor` | Checks `keys.pairing` and `consent.selftest`. | Both checks are removed. `keys.inventory` reports retired keys under `retired`. |
| Gate D | Read-only proof. | Unchanged for Telegram, which stays read-only until 5d. It is joined by the **no-MCP-send guard** (§AI boundary). |
| WhatsVault `INV-APPROVAL` | No WhatsApp write without an out-of-band, user-authenticated approval that no MCP, model, scheduler or provider interface can generate. | **Retired normatively for comms operator sends.** The WhatsVault subtree stays byte-identical, and its old sender stays dormant and unreachable (§Dormant sender). |
| Appendix L | Fourteen safety assertions including consent. | `ConsentConsumedAtMostOnce` and `NoReceiptWithoutVerifiedConsent` are retired and replaced by six (§Formal model). |

## Receipt proof v2

`disclosure_receipts` carries `proof_version INTEGER NOT NULL` (`1` or `2`) and a nullable
`soft_threshold_exceeded` (0/1). A table-level CHECK ties version to shape:

- **v1:** both consent columns non-null, `soft_threshold_exceeded` null. Rebuilt with
  `schema = "tg-mcp-disclosure/v1"` and verified exactly as in v0.1.10.
- **v2:** both consent columns null, `soft_threshold_exceeded` 0 or 1. The signed payload has
  `schema = "tg-mcp-disclosure/v2"`, `authorization_mode = "owner_direct"` and
  `soft_threshold_exceeded` (boolean), and **no** `consent_verified`, `consent_key_id` or
  `consent_challenge_digest`.
- Any other combination fails verification, and the table refuses to store it.

The migration that introduced `proof_version` rebuilt the table append-only: every existing row
became `proof_version = 1` with its consent values byte-identical, and indexes, triggers, foreign
keys, row counts and IDs were preserved. No `authorization_mode` was retrofitted onto history.

The flag is also written to the disclosure's audit event, in `error_code`
(`"soft_threshold_exceeded"` or null), the one free column in the frozen event schema.

## The AI boundary

The spec says exactly this, and makes no stronger claim:

- **MCP capability boundary:** no MCP tool exposes a transmission primitive. A structural test on
  every tool registry proves it: Telegram's ten tools today, WhatsVault's read tools, and any comms
  tools later.
- **Operator CLI:** may transmit directly. From 5d/5e, `comms campaign send` is operator-only.
- **Claude development environment:** the repo's `.claude/settings.json` marks send commands
  **always-ask**. This is a **UX interlock, not the system's authorization boundary**. It does not
  govern other agents.
- **Agents granted unrestricted operator-shell authority are, by definition, inside the operator
  trust boundary.**

Proof: `tests/security/test_ai_boundary.py`.

## Dormant sender

The WhatsVault subtree stays exactly as imported (its tree-hash test keeps holding). No send exists
and none is reachable:

- nothing under `src/comms` imports `whatsvault`, statically or dynamically;
- `apps` (which holds WhatsVault's dispatcher) is not importable from the root environment;
- the root project installs only the `comms` and `telegram-mcp` console scripts;
- `whatsvault.providers` holds only the provider protocol and `fake_meta`; there is no real Meta
  provider (`apps/meta/` is a README);
- neither `src/whatsvault` nor `apps/` imports a network client.

The subtree remains exactly imported, the old sender stays dormant, v0.2 governs the new authority
semantics, and no real WhatsApp send exists yet.

## Formal model

`formal/model.py` has no consent state and no consent transition. The budget tier is chosen
nondeterministically at consult, a v1 receipt is retained in every initial state, and a commit
needs the live per-call reservation. Six assertions replace consent's two:
`OwnerDirectReceiptNeverClaimsConsent`, `ReceiptVersionsDistinguishable`, `V2RequiresOwnerDirect`,
`HardRefusalPrecedesRetrieval`, `ReservationCommitsAtMostOnce` and `NoHandoffBeforeCommitAndAnchor`.
Exhaustive search: **544 reachable states, 22 assertions**, every one holding. Each new assertion
is mutation-tested (`tests/formal/test_invariant_mutations.py`).

## Tombstones

Retired protocol identifiers are tombstoned permanently. They MUST NOT be reassigned to new
structures or semantics. None may appear under `src/` (`tests/security/test_tombstones.py`), and
the frozen-protocol guard counts each removed occurrence by name
(`tests/security/test_comms_protocol_frozen.py`).

| Identifier | What it was |
|---|---|
| `ApprovalEnvelope` | The agent's signed approval (`challenge_sha256`, `sig`, `key_id`) |
| `telegram-mcp-display-v1` | Display-digest domain (`SHA256(domain ‖ JCS(display))`) |
| `tg-mcp-exposure-snapshot/v1` | Exposure-snapshot schema bound into a challenge |
| `RV-1` | The consent rendezvous protocol (HELLO → CHALLENGE → READY) |
| `telegram-mcp-rendezvous/v1` | RV-1 transcript-digest domain |
| `PROMPT` | Daemon → agent prompt frame |
| `APPROVAL` | Agent → daemon approval frame |
| `DENIAL` | Agent → daemon denial frame |
| `telegram-mcp-admin-request/v1` | Request-bound admin approval token domain |
| `telegram-mcp-admin-secret/v1` | Admin approval secret-argument domain |
| `telegram-mcp-sentinel/v1` | Admin approval sentinel-ref domain |
| `PRESENCE_REQUIRED` | Admin error code for a missing presence proof; CLI exit code 6 |
| `consent status` | §33 admin command |
| `consent approve` | §33 admin command |
| `tgu_` | §11.1 consent-challenge ref prefix |
| `telegram-mcp-agent` | Launchd label and process marker of the consent agent |
| `challenge-key`, `agent-approval-key`, `agent-transport-key` | Key purposes; rows retained as `retired` |

## Runbook (outside every gate; owner-approved, not automated)

Delete the paired Secure Enclave approval key and the pairing pins, and uninstall the old signed
consent-agent bundle and its LaunchAgent from this Mac.
