# Telegram MCP Phase 5 Design — Operator Controls, Retention and Recovery

**Status:** Revision 2. Revision 2 gauntleted revision 1 against the shipped
code at `1e21caf` and found 16 defects. They are listed in §0B and fixed in
place. Two of them were proven by running a probe.
Revision 1: four design sections were presented and reviewed one at a time on
2026-09-24, and each section's amendments were checked against the frozen spec
and the shipped code (§0A).
**Date:** 2026-09-24 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`)
**Roadmap:** [release roadmap](../plans/2026-09-22-telegram-mcp-release-roadmap.md), Phase 5 row
**Basis:** Phases 1–4c complete on `main` at `9eec78e`. Gate at that commit:
`1337 passed, 10 skipped`, smoke 53/53, formal 624 states / 18 assertions.

## Controlling statement

**Phase 5 does not change the public MCP tool catalogue, the ten contracts, or
any frozen wire.** TG-JCS-v1, the challenge wire, RV-1, the prompt frames and
`tgml1` stay exactly as they are. Phase 5 adds three things: operator commands
over the existing admin socket, durable lifecycle state, and one new at-rest
format (`tg-mcp-policy-bundle/v1`, which the spec already freezes by name in §33.3).

**SQLite commits define security truth.** Every external action (a Telegram
RPC, a key file, a cache, a file the CLI writes) is a recoverable satellite
around a committed row. If an external step fails after a commit, the result is
a state that is safe and can be reported. It never becomes a state that is
unsafe but looks consistent.

**Nothing here is a production claim.** Phase 5 contributes evidence to Gates F,
H, O, P, Q and R and closes none of them for the release artifact. Installed
clients and the tunnel are Phase 6. The release gauntlet is Phase 7.

## 0. Decisions taken during design review

| # | Decision | Why |
|---|---|---|
| D1 | Decomposition: 5a operator inspect surface and policy engine → 5b lifecycle, rotation, retention, recovery → 5c backup, import, runbooks. Each has its own plan, branch and merge. | 5b reuses 5a's handler shape. 5c's import reuses 5a's simulate/diff. |
| D2 | age-v1 is implemented in-tree on the pinned `cryptography==50.0.1`, X25519 recipients only. There is no new dependency. | Avoids a new native dependency to pin and advisory-review. Interop with stock `age` is proven in both directions (§4.3). |
| D3 | Retention purges run on a daemon schedule **and** a manual `retention purge --now` (Touch ID). | "Phase 5 enforces purge schedules" (Phase-3 design §9.3), plus an operator lever. |
| D4 | `auth revoke-this-session` is wired. `auth.LogOutRequest` becomes the single non-read RPC and can only be reached from one admin handler. | §33 lists it; roadmap Phase 5 owns "local logout versus remote revocation". Phase-4 D8 deferred it here. |
| D5 | `keys rotate <purpose>` covers seven purposes. Principal-identity derivation refuses. | §9.6.1: principal rotation needs "explicit principal migration", and nothing requires one yet. |
| D6 | Policy simulate/diff/import staging runs the **real** mutation handlers inside a rolled-back SAVEPOINT (approach A). | One copy of each rule (CLAUDE.md). A separate pure model would duplicate every mutation rule. A temporary DB copy would put private metadata in a second file. |
| D7 | The recovery **private** identity is used inside daemon memory during import and is never stored. | The daemon must verify the signed ciphertext *and* parse exactly those decrypted bytes. If the CLI decrypted, the daemon would have to trust plaintext it cannot bind to the signature. §33.3 forbids storing the key, not using it. |
| D8 | New operator verbs outside the §33 list: `keys rotate`, `keys trust-backup`, `keys retire-backup`, `retention purge`, and the internal `transfer pull` / `transfer push` (§4.4). New closed audit events: `admin.retention_purge`, `admin.session_revoke`, `admin.account_switch`, `admin.backup_trust`. | The roadmap assigns these capabilities to Phase 5, and §33 says "SHOULD provide", which is not an upper bound. `ADMIN_EVENTS` (`disclosure/audit/chain.py:48`) is a closed vocabulary, so any new event must be added there explicitly. |
| D9 | Deferred by name, not count: `tunnel rotate-binding` (Phase 6), `release verify` (Phase 7), `consent approve` (spec line 927 "MAY"; see G7), and `serve`, whose CLI behaviour is pinned separately (§5.4). | A count invariant hides which command is missing. |

## 0A. Review findings folded into revision 1

Every row was checked against the named artifact before adoption.

| # | Finding | Evidence | Where fixed |
|---|---|---|---|
| R1 | Planning outside the transaction leaves a TOCTOU gap. | Handlers validate and then open `immediate_transaction` (`ipc/handlers/projects.py:92`) | §2.1 three-stage boundary |
| R2 | "Database bytes unchanged" is not a SQLite invariant: WAL and journal bytes move on rollback. | SQLite rollback semantics | §2.3 logical-state leak test |
| R3 | `explain` would rebuild traces outside `authority/`, which is a second policy engine. | `authority/policy.py:189` has a single `evaluate()` and no trace | §2.2 |
| R4 | The §10.4 class/archive switches are evaluated in `disclosure/seams.py:154-166`, outside `authority/`. | `grep include_archived` | §2.2 `PeerFacts` |
| R5 | A staged simulation is in-memory state, so the leak test must name it as the exception. | — | §2.3 `tps_` registry |
| R6 | Presence gating must keep the frozen `PRESENCE_GATED` set. `project instruction` and `disclosure key` are gated even though they read. | `ipc/admin.py` `PRESENCE_GATED` | §2.5 |
| R7 | Revocation was frozen only in memory, so a crash after `LogOutRequest` could lose it. | — | §3.1 two-transaction revoke |
| R8 | `UserDeactivated*` is an unavailable account, not a revoked session. **The shipped adapter already gets this wrong.** | `telegram/telethon_adapter.py:131-138` puts both in `_REVOKED`; spec §27 separates `ACCOUNT_UNAVAILABLE` | §3.2 |
| R9 | `--new-account` must not transplant one account's policy onto another. | `accounts.telegram_user_id` UNIQUE, `projects.account_id NOT NULL` | §3.3 |
| R10 | The sequence "SQL active → then activate secret" leaves metadata pointing at an inactive key after a crash. The key store is one unversioned file per name. | `keys/store.py:125,183` | §3.4 versioned slots |
| R11 | The audit-MAC secret is needed to verify retained events in its epoch, so it cannot be destroyed at rotation. | Phase-3 design §6.2 | §3.4 |
| R12 | Cursor GC must run at startup and at least hourly. The first draft said every 6h. | spec line 1310 | §3.5 |
| R13 | A `message_ref` may be purged only when older than 180 days **and** no live cursor references it. | spec §11.4, `message_ref_retention_days: 180` | §3.5 |
| R14 | Backup-signing public keys must survive for imports, and SQLite cannot see backups held outside it. | §9.6.1 row "policy-backup signing" | §3.5 |
| R15 | A partial drift scan cannot claim `unresolvable`. | — | §3.6 |
| R16 | `account_binding` must use `telegram_user_id`, not the local `accounts.id`. The local ID made fresh-machine restores falsely refuse (same account, different PK) or falsely pass (different account, same PK). | `storage/migrations.py:96-103` | §4.1 |
| R17 | `policy import` is presence-gated, so Touch ID #1 must come before any work. | `PRESENCE_GATED` | §4.4 |
| R18 | `--trust-key` must not become permanent trust as a side effect. | — | §4.4 |
| R19 | The signature input was ambiguous about hex versus raw bytes and single versus double hashing. | — | §4.2 frozen bytes |
| R20 | age tests must cover the whole protocol subset, not just X25519 arithmetic. | C2SP age spec | §4.3 |
| R21 | The bundle may name client *kinds* only, and restore must never create a credential. | `mcp_clients.client_kind` CHECK | §4.1, §4.4 |
| R22 | The formal model must include crash and restart between steps, not only atomic actions. | — | §5.2 |
| R23 | Rotation smoke must prove each purpose's own consequence, not only "receipts verify". | §9.6.1 consequence column | §5.3 |
| R24 | "28 of 31 missing" was a count that coincided with `len(PRESENCE_GATED) == 31`. | `ipc/admin.py` | D9, §5.4 |
| R25 | Found in self-review: the draft sent up to 4 MiB in one admin reply, but the single frame codec caps frames at 64 KiB. | `ipc/framing.py:31` | §4.4 chunked bulk transfer |

## 0B. What revision 2 changed (gauntlet against `1e21caf`)

Every row was established by executing a probe or reading the named line. None was inferred.

| # | Sev | Defect in revision 1 | Evidence | Fix (normative) |
|---|---|---|---|---|
| G1 | **blocker** | The purge order `exposure → receipts → audit` breaks a foreign key. `audit_events.disclosure_ref` references `disclosure_receipts`, so a receipt cannot be deleted while any retained audit event names it. | `storage/migrations.py:369` | §3.5 order: exposure → audit prefix → receipts. A receipt is purged only when its exposure rows **and** its audit event are gone. Receipt retention is therefore a floor: a receipt whose event sits after the truncation root outlives `disclosure_receipt_days`. Doctor warns when `audit_events_days > disclosure_receipt_days`, because receipts would then linger by construction. |
| G2 | **blocker** | The audit chain cannot hold more than one epoch and cannot be truncated. `verify_chain` resets `expected_seq` only on the first row and takes a single key. `append_event` always continues the current epoch. Audit-MAC rotation, restore and retention truncation all depended on this machinery. | Probe `docs/verification/probes/phase5_chain_epochs_probe.py`: three events verify; a hand-built epoch-2 genesis event → `ChainError: chain sequence is not continuous`; deleting seq 1 → the same error. `chain.py:166-268`. | §3.8 (new): `seal_and_open_epoch` and a verifier that is aware of epochs and roots. Phase 5b builds this before any rotation, restore or purge. |
| G3 | high | Rotating `privacy-key` resets every exposure-budget bucket. `subject_digest` HMACs the bucket subject with `privacy-key`, so after rotation the rolling-window sum starts again from zero. That is a hard-limit bypass (Gate P: "hard limits unbypassable"). | `disclosure/budget.py:86-95,105-115` | §3.4: `committed_usage` sums across the digests under the active key and every privacy-key version retired within `rolling_window_minutes` (≤ 1440). Such versions keep their private material until they fall out of the window. A test rotates mid-window and still hits `EXPOSURE_BUDGET_EXCEEDED`. |
| G4 | high | The single wrapper shape cannot hold network handlers. `auth login/status`, `scope discover`, `project drift` and `revoke-this-session` await Telegram, and revoke needs two TXs, but the §2.1 guard forbade `immediate_transaction` outside `_wrapper.py`. | `ipc/handlers/auth.py:53-103`, `scope.py:46-48` | §2.1: two handler kinds, `TxHandler` and `FlowHandler`. Guard: no `await` lexically inside any transaction scope. |
| G5 | high | Audited admin events need the disclosure barrier: `_APPEND_GUARD`, then the TX with `append_event`, then `write_anchor` after commit, with anchor failure sending the daemon to degraded. Revision 1 called `post_commit` "cleanup only" and had no path for this. The shipped lock also appends no event at all: `set_locked` plus `save_epoch_state`, and the latter commits by itself. | `coordinator.py:472-510`, `storage/db.py:290-300`; `grep append_event src` → only `coordinator.py`, `anchor.py` | §2.1: `AuditedTxHandler`. Lock/unlock are rebuilt on it, and `save_epoch_state` stops committing on its own. |
| G6 | high | An admin Touch ID prompt shows only the command name. `action_display = command`, so revision 1's "the prompt shows ciphertext SHA-256, signer, diff…" was false. | `consent/admin_approval.py:103-109`; the agent renders `action_display` up to 160 codepoints (`consent-agent.swift:340`) | §2.7 (new): a bounded summary per command in `action_display`, with the field set unchanged, so no wire change. Full values stay bound through `request_hmac`. |
| G7 | medium | `consent approve` was wired with no semantics. A generic admin Touch ID showing "consent approve" would approve a **disclosure** without showing its client, project or peer, which is weaker than the agent's per-request prompt. | spec line 927 ("MAY exist only if it invokes the same OS user-presence check") | Deferred by name (D9). It stays `NOT_AVAILABLE_IN_PHASE` with that reason. |
| G8 | medium | `project create` requires exactly one account, so `--new-account` would break it. | `ipc/handlers/projects.py:86-88` | §3.3: a durable active-account pointer and one `active_account(conn)` helper. A guard forbids unscoped `FROM accounts` outside `storage/identity.py`. |
| G9 | medium | `key_versions` duplicated `verification_keys`, which already holds public keys with activation and retirement. Revision 1 also missed that secrets load at startup step 3, before the database opens at step 5. | `migrations.py:320-329`; `runtime/lifecycle.py:53-59` | §3.4: `verification_keys` stays the only public registry, plus a `trusted_for_import` column. A new `key_slots` table holds only the private active pointer. Step 3 loads **every** version, and step 8 (`recompute_key_ids`) selects the active one from SQL. |
| G10 | medium | The hourly cursor GC that spec line 1310 requires does not run today. `purge_expired_cursors` has no caller; only `startup_gc` runs. | `grep "purge_expired(" src` → only its own wrapper | §3.5: the maintenance loop calls the **existing** `purge_expired_cursors`. No second copy. Recorded as closing a shipped gap. |
| G11 | medium | `client rotate` sets `enabled = 1`, so it would silently undo `client disable`. It also writes the seed inside the TX without fsync. | `ipc/handlers/clients.py:45-58` | §3.4: rotate refuses a disabled client unless given `--enable`. The seed write follows the versioned-slot order: write, fsync, then TX. |
| G12 | low | A bare `SAVEPOINT` opens a deferred transaction, so the write upgrade can hit `SQLITE_BUSY`. | SQLite transaction semantics | §2.3: `BEGIN IMMEDIATE → SAVEPOINT … → ROLLBACK TO; RELEASE → ROLLBACK`. |
| G13 | low | The guard "only `_decide` reads `mode`" would fail, because `storage/authority_view.py:144` loads it into the view. | `authority_view.py:144` | §2.2: the guard targets *decisions* (comparisons and branches on `mode` / `include_*`), not loads. |
| G14 | low | Stored `PeerFacts` are thinner than revision 1 implied. `peers` stores only `telegram_peer_type`, and archive state is never stored. | `migrations.py:183-196` | §2.2: in snapshots the archive step is always `facts_unknown`, and the class step is decided only where the stored type maps exactly. |
| G15 | low | A canonical peer is the pair `(telegram_peer_type, telegram_peer_id)`, not a single ID. After a restore on a fresh machine, Telethon's entity cache is empty. | `migrations.py:183-226` (`peers`, `peer_policy` keys) | §4.1 carries the pair. The restore runbook runs `auth login` before import, and `project drift` after it. |
| G16 | low | Revision 1 cited "Phase-3 lock logic" and "`admin.lock` events" as existing. Only `set_locked` exists, and nothing appends `admin.lock`. | as G5 | Wording corrected in §2.4. |

## 1. Architecture

```text
admin socket ─► AdminRouter ─► handler wrapper ─┬─► parse(args)                      (pure)
                                                ├─► BEGIN IMMEDIATE
                                                │     plan(conn, parsed)           (reads live state)
                                                │     apply(conn, plan)            (writes, bumps epochs)
                                                ├─► COMMIT
                                                └─► post_commit(result)            (cleanup only)

policy simulate ─► parse ─► SAVEPOINT simulation ─► plan ─► apply ─► EffectiveAccess
                          ─► ROLLBACK TO simulation; RELEASE simulation ─► diff ─► tps_ handle
```

### 1.1 New and changed modules

| Module | Sub-phase | Role |
|---|---|---|
| `ipc/handlers/_wrapper.py` | 5a | The only place a handler transaction commits. Owns `post_commit`. |
| `authority/policy.py` (changed) | 5a | `evaluate` and `evaluate_with_trace` share one implementation. Now takes an optional `PeerFacts`. |
| `authority/effective.py` | 5a | `EffectiveAccess` snapshot and semantic diff. |
| `authority/staging.py` | 5a | `tps_` staged-change registry. |
| `disclosure/seams.py` (changed) | 5a | The class/archive filter is removed and callers go through `authority`. |
| `disclosure/lineage.py` | 5a seam, 5c table | `RestoreLineageLookup`. |
| `ipc/handlers/{lock,audit,disclosure,exposure,consent,policy}.py` | 5a | Newly wired commands. |
| `runtime/lifecycle_state.py` | 5b | Durable account lifecycle (`ACTIVE`, `REVOKING`, `LOGGED_OUT`, `SESSION_REVOKED`, `ACCOUNT_UNAVAILABLE`). |
| `telegram/admin_rpc.py` | 5b | `revoke_session()`, the only caller of `ADMIN_RPCS`. |
| `keys/store.py` (changed), `keys/rotation.py` | 5b | Versioned slots, SQL active pointer, per-purpose consequences. |
| `retention/purge.py`, `retention/schedule.py` | 5b | Ordered purge and maintenance loop. |
| `backup/age.py`, `backup/bundle.py`, `backup/signature.py` | 5c | age-v1 subset, bundle codec, detached signature. |
| `docs/runbooks/*.md`, `scripts/uninstall.sh` | 5c | Operations. |

Schema additions (new migrations, all append-only):

- `account_lifecycle`: state, `remote_revoke`, `session_generation`, `updated_at`.
- `key_slots`: purpose, version, state (`staged|active|retired`), activated_at, retired_at. This is the private-slot pointer only. Public keys stay in the existing `verification_keys`, which gains `trusted_for_import` (G9).
- `account_lifecycle` also holds the singleton `active_account_id` (G8).
- `restore_lineage`: §4.5.
- `maintenance_runs`: kind, started_at, finished_at, outcome, counts. There is no content column.

## 2. Phase 5a — operator inspect surface and one policy engine

### 2.1 Handler boundary

Every mutation handler is split into three functions:

- `parse(args) -> Parsed` is pure and syntactic. It rejects unknown or duplicate keys and bad types. It never touches the connection.
- `plan(conn, parsed) -> Plan` runs **inside** the transaction and resolves refs, `tgl_` handles, current grants and membership against live state.
- `apply(conn, plan) -> Result` writes rows and bumps epochs in SQL.

There are three handler kinds (G4, G5):

- **`TxHandler`**: `parse → BEGIN IMMEDIATE → plan → apply → COMMIT → post_commit`. It is used by every pure policy mutation.
- **`AuditedTxHandler`**: `parse → acquire _APPEND_GUARD → BEGIN IMMEDIATE → plan → apply (includes append_event) → COMMIT → write_anchor → release`. An anchor failure after commit takes the coordinator's step-12 path: the commit stands and the daemon enters `audit_integrity_degraded`. It is used by every command that emits an `ADMIN_EVENTS` event: lock, unlock, key rotation, `audit checkpoint`, repair-anchor, retention purge, session revoke, account switch, backup trust and policy import.
- **`FlowHandler`**: an async sequence of explicit steps. Network and file I/O happen **between** transactions, never inside one, and each DB step goes through the TX or audited-TX runner. It is used by `auth login/status/logout-local`, `auth revoke-this-session`, `scope discover`, `project drift`, `keys rotate` and `policy export/import`.

For all three kinds, `post_commit` does **cleanup only**: every cached `tgl_` handle, staged object and authority artifact is bound to an epoch or snapshot, so the committed epoch bump alone makes stale entries unusable. If `post_commit` raises, the commit stands, the error is logged, and stale entries are still refused.

The existing handlers in `ipc/handlers/{projects,scope,clients,auth}.py` move to the matching kind. Two architecture guards apply:

- no module under `ipc/handlers/` except `_wrapper.py` calls `immediate_transaction`, `commit` or `rollback`;
- no `await` appears lexically inside any `immediate_transaction` block in `src/`, so a transaction can never be held across a network call.

`storage.save_epoch_state` stops calling `commit()` by itself. It becomes an `apply` step.

### 2.2 One evaluator, with traces

`authority/policy.py` exposes `evaluate(view, request, facts=None)` and `evaluate_with_trace(view, request, facts=None)`. Both call one private `_decide(..., trace: list | None)`. The trace is an ordered list of `{step, input_refs, outcome}` over the fixed step order in §33.1: owner mode/deny → class/archive switch → project membership → client grant → egress level → consent/budget requirement.

`PeerFacts` (`kind: private|group|channel`, `archived: bool`) carries the §10.4 class and archive inputs. The filter moves here from `disclosure/seams.py:154-166`, and afterwards that file has no filtering. Retrieval passes live dialog facts. `EffectiveAccess` passes stored facts where the refstore has them. With `facts=None`, the class step records `outcome: facts_unknown` and does not decide.

An AST guard asserts that `_decide` is the only function in `src/` that **compares or branches on** `mode` or the four `include_*` switches. `storage/authority_view.py` may load them (G13).

In a snapshot, stored facts are thin (G14). `peers` holds only `telegram_peer_type`, so the class step decides only where the stored type maps exactly to private, group or channel, and otherwise records `facts_unknown`. Archive state is never stored, so the archive step is always `facts_unknown` in snapshots.

### 2.3 `EffectiveAccess`, simulate, diff

`EffectiveAccess.snapshot(conn) -> Snapshot` enumerates client × enabled project × member peer. Each row carries `client_ref, project_ref, peer_ref, decision, egress, excerpt_max, cross_search, facts_state, trace_digest`, and rows are sorted and hashed with TG-JCS. It is **metadata-effective access**: a truthful answer from stored metadata, not a live oracle. It never calls Telegram and never contains a body, a query or a raw ID.

- `policy explain --client --project [--peer]` returns the matching rows, each with its full trace.
- `policy simulate <command> [--arg K=V ...]` accepts only policy mutations: `scope *`, `project *` mutations and `client disable`. It runs `parse → BEGIN IMMEDIATE → SAVEPOINT simulation → plan → apply → snapshot → ROLLBACK TO simulation; RELEASE simulation → ROLLBACK` (G12), then diffs the before and after snapshots. It returns the diff and a `tps_` handle.
- `policy diff <tps_…>` recomputes the diff and refuses if the handle's base digest no longer matches.

**`tps_` registry.** Handles live in daemon memory, expire after 10 minutes, and are bound to the admin peer credential and the active account. Each records a base digest over `(policy_epoch, every project_epoch, security_epoch, snapshot digest)` and holds only the parsed command or bundle plan and the diff. It contains no bodies and no raw IDs.

**Leak test.** After simulate, the logical state is identical: every table row, all epochs, refs, settings, audit rows, lock and lifecycle state, and every security cache. The only in-memory difference is the new `tps_` entry.

**Simulate-equals-commit.** For every policy command and a generated set of arguments, the diff from simulate must equal the diff between the snapshot before and after actually committing the same command.

### 2.4 Commands wired in 5a

| Command | Presence | Notes |
|---|---|---|
| `lock`, `unlock` | yes | `AuditedTxHandler`. It wraps `authority.set_locked` and, **new in 5a**, appends `admin.lock` / `admin.unlock` and refreshes the anchor (G5, G16). |
| `lock status` | no | |
| `audit verify` | no | Also recognises truncation at a verified checkpoint (§3.5). |
| `audit checkpoint`, `audit repair-anchor` | yes | `write_checkpoint` and `repair_anchor` (`chain.py:281`, `anchor.py:175`), now reachable through the daemon. |
| `disclosure show`, `disclosure verify` | no | `show` consults `RestoreLineageLookup` (§2.6). |
| `disclosure key` | yes | Frozen gating kept. |
| `exposure status` | no | |
| `consent status` | no | |
| `scope remove` | yes | |
| `project rename`, `project remove-peer`, `project grant-cross-search`, `project revoke-cross-search` | yes | `remove-peer` takes a `tgl_` handle only. |
| `project members` | no | Mints `tgl_` handles bound to the project and snapshot epoch. |
| `project overlap` | no | Canonical peers shared between projects, with the `shared` flag. |
| `project instruction` | yes | Frozen gating kept. |
| `client disable` | yes | |
| `policy explain`, `policy simulate`, `policy diff` | no | Operator-authenticated by the admin socket, as §33.1 requires. |

### 2.5 Presence

Every command keeps its frozen `PRESENCE_GATED` classification. Phase 5 adds gated entries only for its new verbs (`keys rotate`, `keys trust-backup`, `keys retire-backup`, `retention purge`, `project drift`), each with a reason recorded next to the set. A test pins the whole set by name.

### 2.6 Restore-lineage seam

`RestoreLineageLookup.affecting(receipt) -> LineageVerdict` answers `none` in 5a. `disclosure show` already renders `payload_unreconstructable` when the lookup says so, and 5c replaces the lookup with the table-backed version.

### 2.7 Touch ID summaries (G6)

Admin approvals keep the frozen display field set. Each gated command supplies a deterministic `action_display` summary of at most 160 codepoints: the command, then a fixed set of salient values, with long values shortened to `first8…last4`. For example:

- `keys rotate audit-mac · new chain epoch 3`
- `policy import · ct 3a9f01c2…e21c · signer ed25519:1a2b3c4d…9f0e · trust-once`
- `policy import commit · +4/−1 grants · 2 projects disabled · 0 peers removed`
- `retention purge · root ckp_…`

The full values are bound cryptographically by `request_hmac` over the exact arguments, as they already are.

Secret arguments (the age identity, like the 2FA password today) are stripped before display. They enter `request_hmac` only as `HMAC(privacy-key, value)`.

A test renders every summary through the agent's own `renderableText` rule and asserts that it fits within 160 codepoints unchanged.

## 3. Phase 5b — lifecycle, rotation, retention, recovery

### 3.1 Local logout versus remote revocation

`auth logout-local` (already exists) wipes the local session and moves the account to `LOGGED_OUT`.

`auth revoke-this-session` (Touch ID) runs in this order:

1. **TX1:** `lifecycle = REVOKING`, `security_epoch += 1`, and append `admin.session_revoke` (phase `started`). From this commit onward, all nine sensitive tools answer `SESSION_REVOKED` before retrieval, and every earlier cursor, consent and lease is dead, even if the daemon crashes on the next instruction.
2. One `auth.LogOutRequest` through `telegram/admin_rpc.revoke_session()`. There is no retry, and the outcome is recorded as `confirmed`, `failed` or `unknown`.
3. Wipe the local session regardless of the outcome.
4. **TX2:** `lifecycle = LOGGED_OUT`, record `remote_revoke`, and append `admin.session_revoke` (phase `finished`).

At startup, a `REVOKING` state finishes steps 3–4 with `remote_revoke = unknown` and never resends.

**RPC split.** The adapter allowlist becomes `READ_RPCS` (what tool paths may send) and `ADMIN_RPCS = {auth.LogOutRequest}`. The MCP-facing `TelegramReadService` gains no method. `revoke_session()` lives in `telegram/admin_rpc.py`. Two proofs cover it: the AST guard shows `admin_rpc` is imported only by `ipc/handlers/auth.py`, and the runtime outbound-RPC recorder, run across every tool test, never observes an `ADMIN_RPCS` constructor.

### 3.2 Error mapping (fixes a shipped defect)

| Telegram / Telethon | Gateway code | Lifecycle transition |
|---|---|---|
| `AuthKeyUnregistered`, `SessionRevoked`, `SessionExpired`, `AuthKeyDuplicated` | `SESSION_REVOKED` | → `SESSION_REVOKED` (durable) |
| `UserDeactivated`, `UserDeactivatedBan` | `ACCOUNT_UNAVAILABLE` | → `ACCOUNT_UNAVAILABLE` (durable) |
| Telethon non-RPC `AuthKeyNotFound` | `TELEGRAM_UNAVAILABLE` | none; can be temporary |

This corrects `_REVOKED` in `telegram/telethon_adapter.py:131-138` and adds a regression test for each row.

### 3.3 Recovery (§43.2)

From `SESSION_REVOKED`, `LOGGED_OUT` or `ACCOUNT_UNAVAILABLE`, recovery is `auth login`. Each successful login increments `session_generation` and `security_epoch`.

If the login resolves to a different `telegram_user_id` than the active account, it refuses unless the operator passes `--new-account`. With `--new-account`, one TX:

- inserts a new `accounts` row with fresh default-deny `policy_state`;
- switches the durable `active_account_id` pointer. Every "the account" read goes through one `active_account(conn)` helper, which replaces `projects.py:86`'s "exactly one account" check, and a guard forbids unscoped `FROM accounts` outside `storage/identity.py` (G8);
- disables the old account's projects without rewriting their `account_id`;
- inherits no scope lists or grants;
- keeps old account rows, refs and receipts for verification;
- bumps `security_epoch`;
- appends `admin.account_switch`.

### 3.4 Key rotation

`keys rotate <purpose>` requires Touch ID.

| Purpose (registry name) | Consequence, applied in the rotation TX |
|---|---|
| cursor (`cursor-key`) | All cursors invalidated. |
| scope-digest (`privacy-key`) | Digest-bound cursors and pending consents invalidated. **Budget continuity (G3):** `committed_usage` sums over the bucket digest under the active key and under every `privacy-key` version retired within `rolling_window_minutes`. |
| disclosure signing (`disclosure-key`) | New key ID. The old public key is retired and kept through receipt retention + grace. |
| checkpoint signing (`audit-checkpoint-key`) | New key ID. The old public key is kept through checkpoint retention + grace. |
| backup signing (`backup-key`) | New key ID. The old public key is kept and marked `trusted_for_import` (§3.5). |
| challenge signing (`challenge-key`) | Pending challenges invalidated. The agent must re-pin through the existing pairing path; owner-run evidence (§5.5). |
| audit MAC (`audit-chain-key`) | A signed checkpoint is sealed and a new chain epoch opened. Its first event is `admin.key_rotation`. |
| principal (`principal-key`) | **Refused** with a documented reason (D5). |

**Versioned slots.** `keys/store.py` moves from one file per name to `<name>/<version>`, where each file is immutable and 0600. The SQL table `key_slots` owns the active pointer. Public halves stay in the existing `verification_keys` (G9).

Startup step 3 (`load_secrets`) loads **every** version of each purpose into memory. Step 8 (`recompute_key_ids`) selects the active version from `key_slots` once the database is open. `load_key(name)` returns that selection, and retired versions are reachable only by explicit version.

The rotation sequence:

1. Write the new version and fsync the file and its directory.
2. Load it back and check that its key ID recomputes.
3. `BEGIN IMMEDIATE`: register the version, apply the purpose's consequence, switch the pointer, append `admin.key_rotation`, `COMMIT`.
4. Reload the active key from the version SQL selects.

If the process crashes before step 3, the new version is an orphan, which doctor reports and `keys rotate` cleans up on the next run. If it crashes after step 3, both versions exist and SQL picks the new one unambiguously.

Retired **private** material is deleted only when its purpose allows:

- **Signing keys** can go immediately, because what they need to keep is public.
- **The cursor key** goes at once, because its consequence is invalidation.
- **A scope-digest (`privacy-key`) version** is kept until it falls out of `rolling_window_minutes`, for budget continuity (G3). The audit-MAC key is kept until every retained event in its epoch sits behind a verified truncation checkpoint (§3.5).

A one-time migration moves each existing single file to version `1` and registers it as active.

**Client seeds (G11).** `client rotate` refuses a disabled client unless given `--enable`. Today it sets `enabled = 1` unconditionally, which would undo `client disable`. Its seed write follows the same order as the key rotation: write the file and fsync it, then run the TX. Today the seed is written inside the TX without fsync.

### 3.5 Retention and maintenance

There are two maintenance cadences:

- **Cursor GC** runs at startup and hourly, deleting only cursors past `expires_at` (spec line 1310). It calls the **existing** `storage.purge_expired_cursors`, which has no caller today, so this closes a shipped gap (G10).
- **The retention purge** runs at startup and every 6h while idle, and on `retention purge --now` (Touch ID).

The retention purge runs under the audit append guard in this fixed order:

1. Exposure rows older than `exposure_ledger_days`.
2. The audit prefix: choose the **newest verified signed checkpoint at or before** the `audit_events_days` cutoff as the truncation root. Keep the root checkpoint and its `last_event` row, and delete only events strictly before that row (§3.8). Checkpoints themselves are purged past `audit_checkpoint_days`, but never the current root or an epoch-sealing checkpoint that still has retained events after it.
3. Receipts older than `disclosure_receipt_days` whose exposure rows **and** audit event are gone. Both foreign keys (`migrations.py:316,369`) enforce this, so the order is mechanical, not a convention (G1). Receipt retention is a floor.
4. `message_ref` rows whose `last_used_at` is older than `message_ref_days` **and** that no live cursor references. Cursor `state_json` holds no message refs (`ALLOWED_STATE_KEYS`), so the second condition is checked but is currently vacuous.
5. Public keys:
   - disclosure keys after receipt retention + grace;
   - checkpoint keys after checkpoint retention + grace;
   - backup keys are **never** auto-purged, only removed by `keys retire-backup <key_id>` (Touch ID).
   - A key that a retained receipt or checkpoint still references is never purged.
6. Retired audit-MAC secrets whose epoch has no retained events left.
7. Append `admin.retention_purge` with counts per phase and the root checkpoint ID, then record `maintenance_runs`.

**Failure handling.** If the root checkpoint fails verification with a real MAC or signature mismatch, the purge enters the existing `audit_integrity_degraded` latch and deletes nothing. If it is blocked for a benign reason (no eligible checkpoint yet), the run records `blocked` and doctor reports it.

`audit verify` reports `truncated_at_verified_checkpoint <id>` as distinct from a chain break.

### 3.6 `project drift`

`project drift` requires Touch ID because it enumerates Telegram. It uses the existing bounded dialog scan and returns `{complete, scanned, partial_reason}`. For each member it reports `renamed`, `owner_denied`, `class_excluded`, `unresolvable` (only when `complete = true`) or `unobserved` (otherwise). It never mutates.

### 3.7 Doctor

These checks replace phase-skips:

- Telegram auth and lifecycle state;
- credential-checked endpoint refusal;
- key inventory: purpose separation, active versions, orphans, rotation state;
- historical-key coverage: every retained receipt and checkpoint has its public key;
- the last run of each maintenance cadence, its outcome, and whether it is overdue;
- project integrity: unique slugs and refs, no enabled peer bypassing owner deny, FK and `quick_check`.

`tunnel.tls_trust` stays skipped until Phase 6. Nothing prints secrets, phone numbers, raw IDs or content.

### 3.8 Chain epochs and truncation (G2)

Today `disclosure/audit/chain.py` supports one epoch from genesis and nothing else, as the probe showed. 5b adds the following, test-first, **before** any rotation, restore or purge code:

- **`seal_and_open_epoch(conn, *, new_epoch_event)`.** Inside the caller's TX:
  1. write a signed checkpoint at the current head (the *sealing* checkpoint);
  2. append `new_epoch_event` at `(epoch + 1, seq 1)` with `prev = genesis_mac(epoch + 1)`, MACed under the key for the new epoch.

  `append_event` is unchanged and continues whatever epoch the head is in.
- **`verify_chain(conn, keys_by_epoch, *, root=None)`:**
  - The sequence resets at each new epoch: seq 1 with `prev = genesis_mac(epoch)`.
  - Epoch numbers must be contiguous from the first retained epoch.
  - Every epoch except the last must end exactly at a sealing checkpoint whose signature verifies and whose `last_event_id` / `last_event_mac` equal the epoch's last retained event.
  - With `root` (the truncation checkpoint), verification starts at the root's `last_event` row: its signature must verify and its MAC must equal the row's `event_mac`. The walk continues from there, and no genesis is required for the root's epoch.
  - Anything else is a `ChainError`.
- **Why contiguity plus sealing checkpoints is enough.** Deleting a whole middle epoch leaves an epoch-number gap. Deleting an epoch's tail leaves its sealing checkpoint mismatched. Deleting the newest events is caught by the existing external anchor. `genesis_mac` stays frozen, and no cross-epoch MAC field is added: `audit_events` columns are §12.2 and must not change.
- **Keys by epoch.** Each epoch's MAC key version is recorded in `key_slots`, and verification uses exactly that version (§3.4).
- **Probe as regression.** `docs/verification/probes/phase5_chain_epochs_probe.py` becomes `tests/disclosure/test_chain_epochs.py`. Its two rejections must turn into acceptances for legitimate epochs and roots, and a family of negatives covers the attacks: a missing middle epoch, a truncated tail, a forged root, a root without its row, and an epoch number that skips.

## 4. Phase 5c — backup, import, runbooks

### 4.1 Payload `tg-mcp-policy-bundle/v1`

The payload is TG-JCS bytes produced by the single encoder and read back through the single strict decoder. Fields:

- `schema`, `created_at`
- `account_binding` = lowercase hex of `SHA256("tg-mcp-account-binding/v1" || 0x00 || u64_be(telegram_user_id))`
- `policy_state`: `mode` and the four `include_*` switches
- `owner_allow`, `owner_deny`: canonical peers as `(telegram_peer_type, telegram_peer_id)` pairs, which §33.3 permits inside the ciphertext (G15)
- `projects`: slug, display name, enabled, and members as canonical pairs with `shared`
- `grants`: `client_kind` (one of `openai_tunnel`, `codex_local`, `claude_code_local`), project slug, egress, `excerpt_max`, `cross_search`
- `settings`: non-secret rows only

A plaintext-scan test provisions every secret in the §33.3 exclusion list in the fixture: session, `api_hash`, lease seeds, auth bindings, tunnel keys, consent keys, private signing and HMAC keys. It then asserts that none of them appears in the plaintext, along with no message-body marker.

### 4.2 Detached signature `tg-mcp-policy-signature/v1`

```text
digest         = SHA256(ciphertext)                       # 32 raw bytes
signing_input  = ASCII("tg-mcp-policy-signature/v1") || 0x00 || digest
signature      = Ed25519.Sign(backup_key, signing_input)
sidecar (TG-JCS) = { "alg": "ed25519",
                     "ciphertext_sha256": hex(digest),
                     "key_id": "ed25519:sha256:<hex>",
                     "public_key": base64(raw 32 bytes),
                     "schema": "tg-mcp-policy-signature/v1",
                     "signature": base64(64 bytes) }
```

A golden vector (a fixed key, ciphertext, `signing_input` hex and signature) pins these bytes.

### 4.3 age-v1 subset (`backup/age.py`)

The subset is X25519 recipient stanzas only: no scrypt, no plugins, no armor. The implementation covers:

- the 16-byte file key;
- ephemeral X25519 with rejection of an all-zero shared secret;
- HKDF-SHA-256 with salt `ephemeral_share || recipient` and info `age-encryption.org/v1/X25519`;
- the ChaCha20-Poly1305 wrap with a 32-byte body;
- the header MAC (HKDF info `header`, HMAC-SHA-256), verified before any payload byte is released;
- a 16-byte payload nonce and 64 KiB STREAM chunks with the last-chunk flag.

Parsing is canonical: exact base64 without padding, the exact line structure, and no trailing data.

Tests:

- **Vendored C2SP age testkit vectors** for the X25519 subset, with the source commit and each file's SHA-256 recorded in `tests/fixtures/age/SOURCE.md`. They always run.
- **Named negatives:** non-canonical base64; wrong stanza body length; all-zero shared secret; header MAC altered; stanza altered; payload chunk altered; truncated final chunk; missing last-chunk flag; extra trailing bytes; wrong identity.
- **Interop in both directions:** our encrypt → `age -d`, and `age -e` → our decrypt. If `/opt/homebrew/bin/age` is absent, these two are reported as skipped with a reason and never as passed.

### 4.4 Export and import

**Export** (`policy export --recipient age1… --output <file>`, Touch ID):

1. The daemon builds the payload, encrypts it to the recipient and signs it with the active backup key.
2. It returns the sidecar plus a transfer descriptor `{transfer_id, total_bytes, ciphertext_sha256}`. Plaintext never leaves the daemon.
3. The CLI pulls the ciphertext in 32 KiB chunks (see "Bulk transfer" below) and checks `total_bytes` and the SHA-256.
4. The CLI writes `<file>` and `<file>.sig` 0600 with the atomic-rename pattern and prints the signer fingerprint for the operator to record out of band.

**Import** (`policy import <file> --signature <file.sig> --identity <path|->  [--trust-key ed25519:sha256:…]`):

1. **Touch ID #1**, before any other work. The `request_hmac` binds the pushed ciphertext's SHA-256, the sidecar digest, the signer fingerprint, the `--trust-key` fingerprint if given, and `HMAC(privacy-key, identity)`. The prompt shows the §2.7 summary: shortened ciphertext digest, signer, and `trust-once` when `--trust-key` is given. It never shows the identity.
2. **Trust check:** `recompute(public_key) == key_id`, and either `key_id` is registered with `trusted_for_import` or `key_id == --trust-key`. `--trust-key` authorises **this staged import only** and writes nothing to the registry. Permanent trust is the separate `keys trust-backup <key_id>` (Touch ID; `admin.backup_trust`).
3. Verify the signature, then decrypt in daemon memory.
4. Strict-decode the payload, and check `account_binding` against the active account. A mismatch refuses.
5. `parse → SAVEPOINT → plan → apply → snapshot → ROLLBACK TO / RELEASE`. Stage a `tps_` handle (§2.3) and return the diff.
6. `policy import --commit tps_…`: **Touch ID #2**, whose §2.7 summary gives the diff counts. Then one audited `BEGIN IMMEDIATE`:
   - base-digest recheck;
   - `plan → apply` as a replace;
   - bump the policy epoch, every project epoch, every grant epoch and the security epoch;
   - insert a `restore_lineage` row;
   - `seal_and_open_epoch` (§3.8), with `admin.policy_import` as the new epoch's first event;
   - `COMMIT`.

**Bulk transfer.** Admin frames are capped at 64 KiB (`ipc/framing.py:31`), and that codec exists once and is not changed. Ciphertext therefore moves as a sequence of ordinary frames through two internal admin commands, `transfer pull <id> <n>` and `transfer push <id> <n>`. Each carries at most 32 KiB of base64 payload. A transfer:

- lives in daemon memory, bound to the admin peer credential;
- expires after 5 minutes;
- is capped at 4 MiB total;
- must arrive as a complete, in-order, SHA-256-checked sequence before any use.

For import, the CLI pushes the ciphertext first. The `policy import` request then names the `transfer_id`, and step 1's prompt shows the SHA-256 of the pushed bytes. The sidecar and identity are small and travel inline in the `policy import` frame. The two transfer commands are ungated: they move opaque bytes and authorise nothing. They are listed in D8.

**Identity handling.** The identity is read from a file path or stdin only, never argv or env. It is length-bounded and parsed strictly as an `AGE-SECRET-KEY-1…` line. The daemon drops its references after decryption and never logs or persists it. There is no zeroisation claim, because Python cannot honour one.

**Replace semantics.** The resulting policy equals the bundle:

- Projects missing from the bundle are **disabled**, not deleted.
- Grants map onto locally provisioned clients by `client_kind`. A kind with no local client stays unresolved. Restore **never** creates a credential, so an unprovisioned client cannot become operational.
- Refs are reused where the canonical peer already has one for this account and minted otherwise. Each minted class is recorded in the lineage row.

### 4.5 Restore lineage

`restore_lineage` has these columns: `restore_id, committed_at, bundle_ciphertext_sha256, old_chain_epoch, new_chain_epoch, restore_event_id, regenerated_ref_classes, counts`. It stores no plaintext, no identity and no raw IDs. `RestoreLineageLookup` then answers `payload_unreconstructable` for any receipt from a chain epoch before a restore that regenerated a ref class the receipt uses. That result is distinct from a signature failure.

### 4.6 Runbooks and uninstall

`docs/runbooks/` contains:

- `install.md`
- `uninstall.md`, with a state-ownership table: what is deleted, what is kept, and which Keychain items are involved
- `restore-from-backup.md` (lost or replaced Mac): install, then `auth login` (an active account must exist), then `policy import`, then `project drift` so the empty Telethon entity cache gets filled (G15)
- `session-compromise.md` (revoke)
- `key-compromise.md` (one section per purpose)
- `audit-degraded.md` (repair-anchor)
- `session-revoked-or-account-unavailable.md`

`scripts/uninstall.sh` is idempotent, prints its plan with `--dry-run` like the installers, and never deletes the operator's recovery identity or backups.

A test parses every `telegram-mcp …` command in every runbook and asserts that each one exists in the CLI parser or `ADMIN_COMMANDS` and has a handler, so none answers `NOT_AVAILABLE_IN_PHASE`.

## 5. Verification, evidence and sequencing

### 5.1 Sequencing

Each of 5a, 5b and 5c gets a plan (writing-plans), a `phase-5x` branch, the staged gate, a `--no-ff` merge to `main`, an evidence update, and AGENT.md and CHANGELOG entries. 5a merges before 5b is planned, and 5b merges before 5c is planned. Commits happen only on a green gate.

### 5.2 Failure injection and the formal model

**Crash points.** Each boundary below has a named crash point, injected through seams, never through flags in production paths. After each crash the test **restarts the daemon for real** and asserts the recovered state:

- key version written, key TX committed, key reloaded;
- `REVOKING` committed, `LogOutRequest` sent, session wiped;
- after each purge phase;
- truncation root installed but purge event not yet appended;
- import TX mid-apply;
- `post_commit` raising.

**Formal model** (`formal/model.py`, bounded, with the state count recorded):

- **New durable states:** chain epochs and sealing checkpoints (§3.8); lifecycle `REVOKING`; key versions `staged / registered / active`; restore `committed` with stale caches; truncation `root_installed` / `event_appended`.
- **New transitions:** `revoke`, `rotate_audit_mac`, `retention_truncate`, `restore_commit`, `Crash`, `Restart`.
- **New assertions:**
  - `CommittedRevokingSurvivesRestart`: no disclosure handoff after a committed `REVOKING`, across any crash and restart.
  - `ActiveKeyAlwaysLoadableAfterRestart`.
  - `RestoreCommitInvalidatesOldAuthorityAcrossRestart`: no pre-restore or pre-rotation authority object is accepted.
  - `PurgeNeverCreatesUnauthenticatedAuditPrefix`: truncation happens only at a verified checkpoint, and verification tells truncation apart from a break.
  - `OldAuditMacRetainedWhileNeeded`.

### 5.3 Suites and smoke

**Suite additions:**

- simulate-equals-commit;
- the logical-state leak test;
- the one-evaluator AST guard;
- the handler-transaction guard;
- `ADMIN_RPCS` reachability (AST plus recorder);
- the §3.2 error-mapping regression;
- expired artifacts: receipts verify after a disclosure-key rotation until retention + grace and are then purged, and backup keys are never auto-purged;
- the content-leak sweep extended to purge events, lineage rows, `maintenance_runs`, doctor output and the bundle plaintext;
- the runbook command-existence test;
- the signature golden vector;
- the chain-epoch and truncation family (§3.8), grown from the G2 probe;
- a budget test that rotates `privacy-key` mid-window (G3);
- the no-`await`-in-transaction guard (G4);
- an anchor-failure test for every audited admin command (G5);
- a test fitting every §2.7 summary into 160 codepoints (G6);
- the purge FK order: a receipt whose audit event is retained is not purged (G1);
- the age vector and negative suites.

**Smoke rows** drive the shipped artifacts:

- `policy simulate` → `policy diff`, then commit; the post-commit diff equals the simulated one.
- `lock` → sensitive tool refused → `unlock`.
- Rotation, **one row per purpose, each proving its own consequence:**
  - cursor: old cursor → `INVALID_CURSOR`
  - scope-digest: old cursor and pending consent rejected
  - disclosure: an old receipt still verifies
  - checkpoint: an old checkpoint still verifies
  - backup: a bundle signed before rotation still imports
  - audit MAC: old epoch sealed, new epoch begins, `audit verify` passes across both
  - challenge: pending challenge dies; re-pairing is owner-run (§5.5)
- `retention purge --now` → `audit verify` reports `truncated_at_verified_checkpoint`.
- `auth revoke-this-session` on the fake transport → `SESSION_REVOKED` → `auth login` → reads succeed with a new `session_generation`.
- `policy export` → stock `age -d` recovers the TG-JCS plaintext → `policy import --trust-key …` into a **fresh** database → `--commit` → `EffectiveAccess` equal to the source.

### 5.4 Command completeness, by name

A test computes the §33 command list, subtracts the deferred set `{tunnel rotate-binding, release verify, consent approve}`, and requires that every remaining command is either an `AdminRouter` command with a registered handler or a top-level CLI verb that works. It subtracts `consent approve` too (G7) and asserts all three deferred commands answer `NOT_AVAILABLE_IN_PHASE` by name.

`serve` is a CLI concern, not an `AdminRouter` handler. Its CLI behaviour is pinned separately: it prints the pointer to `start` / `demo` and exits with `EXIT_NOT_IN_PHASE`. Phase 6 decides whether `serve` becomes a real alias.

### 5.5 Owner-run evidence

These rows are recorded in the gate ledger as owner-run, never as passed by CI:

- **Test DC revoke.** Use a **disposable** session created for this test only, never one another harness depends on: log in, prove one read, `revoke-this-session`, prove a durable `SESSION_REVOKED` across a daemon restart, then `auth login` to a new session generation. Extends `tests/telegram/test_testdc.py` behind `--run-telegram-testdc`.
- **Test DC drift:** a real dialog scan, including a partial scan under a tight RPC budget.
- **Challenge-key rotation re-pairing** with the real consent agent (`--run-platform-gated`).

### 5.6 Gates

`docs/verification/phase-5.md` carries the gate ledger in the established format, and every line names its proving command. F, H, O, P, Q and R move. Anything that depends on Phase 6/7 or on owner-run hardware stays **PARTIAL** and names what is missing.

**Done means:**

- §5.4 is green;
- the full gate is green: `uv sync --locked`, contract check, `pytest`, smoke, formal, ruff check, ruff format, mypy, build;
- the evidence is written;
- AGENT.md and CHANGELOG entries are made for each sub-phase.

## 6. Claim boundaries

- `EffectiveAccess` is metadata-effective access. Live divergence is `project drift`'s job.
- `remote_revoke = confirmed` means Telegram acknowledged `LogOutRequest`. It does not mean every other device lost access.
- A backup proves the policy that was signed and encrypted. It says nothing about messages, and it is not a session backup.
- Retention truncation is authenticated by a checkpoint. It does not prove that the deleted events said anything in particular.
- No zeroisation of secrets in Python memory is claimed.

## 7. Out of scope

Installed Codex and Claude builds, the tunnel, mTLS ingress and `tunnel rotate-binding` belong to Phase 6. `release verify`, SBOM and signed release evidence belong to Phase 7. Principal-key rotation and migration are out. So are scrypt/passphrase, plugin and armored age recipients. So is merge-style import. So is any change to the ten tool contracts.

## Normative grounding

Spec: §9.6.1 key inventory, §10.4 read scope, §11.4 retention, cursor GC at line 1310, §23A.3 receipt retention, §26.5 audit chain, §27 error codes, §33 CLI, §33.1–§33.4, §43 recovery. Phase-3 design: §6.2 epochs, §6.5 closed vocabulary, §9.3 purge ordering. Phase-4 design: D8. Roadmap: Phase 5 row and engineering decision 9.
