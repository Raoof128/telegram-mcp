# Telegram MCP Phase 4 Design — Allowlisted Telegram Adapter and Vertical Tool Slices

**Status:** Revision 1. Four design sections were presented and reviewed one at
a time; every amendment from those reviews is folded in below. Written for a
line-by-line gauntlet against V0.1.10, the roadmap and the Phase-3 design before
any plan is written.
**Date:** 2026-09-23 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`)
**Roadmap:** [release roadmap](../plans/2026-09-22-telegram-mcp-release-roadmap.md), Phase 4 row
**Basis:** Phases 1–3 complete on `main` at `1efc633`; verified fresh for this
design — `626 passed, 7 skipped`.

## Controlling statement

**Phase 4 does not change the public MCP tool catalogue, the schema or any
frozen wire.** The ten contracts, the eighteen tables, TG-JCS-v1, the challenge
wire, RV-1, the prompt frames and `tgml1` stay exactly as they are. Phase 4
supplies what Phase 3 deliberately left as seams: a real authority, a real
consent binding, a real caller identity and real retrieval.

**No tool may bypass the disclosure seam** (Phase-3 design, controlling
statement). Every sensitive success in this phase is a released
`DisclosureOutcome`: data and receipt in one object. A slice that reaches around
the coordinator is a defect.

**Nothing here is a production claim.** Phase 4 contributes evidence to Gates
B, C, D, E, F, G, H and N and extends O–Q. It closes none of them for the
release artifact; installed clients are Phase 6 and the release gauntlet is
Phase 7.

## 0. Decisions taken during design review

| # | Decision | Why |
|---|---|---|
| D1 | Telegram access: Test DC first, a dedicated non-primary account for the 4c qualification run. The primary account is never used by any test. | Spec §38.15; primary credentials never required for CI. |
| D2 | Dev `api_hash` lives in a login-Keychain generic-password item the owner creates by hand. It is read once at daemon start and held only in memory. Production moves it to the `telegram-mcpd` store. | §9.1 forbids YAML, `.env`, argv, logs and repo files; the service account is not yet installed on this Mac. |
| D3 | Decomposition: 4a seams and ingress (no network) → 4b adapter, login, four Telegram tools → 4c context, both searches, qualification. | The riskiest integration (fakes → real seams) is proven before any network code exists. |
| D4 | The authenticated runtime ingress lands in 4a, not Phase 6. | A "first real sensitive success" that skips bearer → principal is half synthetic. Phase 6 proves installed clients, not the ingress's existence. |
| D5 | The request nonce belongs to the consent challenge, not to `freeze_arguments`. | Spec §9.8 step 4 and §23C.3. Phase-3 code currently reserves with `request.nonce`; 4a corrects it. |
| D6 | `PrincipalContext` is identity only. Grants are authority, read at step 2 and again at step 8. | A grant frozen at ingress would survive a revoke issued while the prompt is open. |
| D7 | Search continuation is round-robin over a bounded peer window, not a global k-way merge. | 250 eligible peers against 20 RPCs per call: the global top-K is unknowable without exhaustive scan; a heap only hides that. |
| D8 | `revoke-this-session` stays unavailable until Phase 5. | Roadmap assigns remote revocation to Phase 5; a half-working destructive command is worse than none. |

## 1. Architecture

```text
loopback HTTP (runtime ingress)
  └─ transport guards (Host/Origin/body limit/duplicate keys)     — shared module
  └─ verify_lease (tgml1)  → PrincipalContext (identity only)
  └─ dispatch: name allowlist + validate_arguments               — unchanged rules
  └─ SensitiveDispatcher → DisclosureCoordinator.disclose(...)
        ├─ CoordinatorAuthority   (Phase-2 policy, epochs, cursors, egress)
        ├─ CoordinatorConsent     (Phase-2 broker, real agent)
        └─ RetrievalAdapter → TelegramReadService
              ├─ MetadataReadAdapter   list_projects, resolve_project   (4a)
              └─ TelethonReadAdapter   the other seven                 (4b, 4c)
  └─ DisclosureOutcome → success_result / error_result
```

**Composition is the only wiring point.** `runtime/composition.py` is the one
module that constructs a coordinator, hands it a `RetrievalAdapter`, and gives
the `SensitiveDispatcher` the coordinator. Neither `dispatch` nor `tools/*`
imports a concrete adapter, and `SensitiveDispatcher` never holds a retrieval
object it could call on its own. An AST test enforces both (§6.1).

**The demo server stays synthetic.** `server.py` keeps returning
`POLICY_UNCONFIGURED` for every sensitive tool (§8.4). A structural test
asserts its route table has no path to `SensitiveDispatcher`, and that it
imports neither `runtime.composition` nor any adapter.

### 1.1 New and changed modules

| Module | Sub-phase | Role |
|---|---|---|
| `http_guards.py` | 4a | Host/Origin/body-limit/duplicate-key middleware, moved out of `server.py` so the demo and the ingress import one copy. |
| `runtime/ingress.py` | 4a | Loopback coding-client endpoint (§8.2.1); bearer verification before dispatch. |
| `runtime/composition.py` | 4a | The only place the coordinator, seams and adapters are wired. |
| `sensitive_dispatch.py` | 4a | Async; validated args + `PrincipalContext` → `coordinator.disclose` → MCP result. |
| `disclosure/seams.py` | 4a | `CoordinatorAuthority`, `CoordinatorConsent`, per-tool worst-case estimators. |
| `telegram/service.py` | 4a | `TelegramReadService` protocol and domain dataclasses (§35). No Telethon. |
| `telegram/metadata.py` | 4a | `MetadataReadAdapter`: `list_projects`, `resolve_project` from SQLite. |
| `ipc/handlers/projects.py`, `ipc/handlers/scope.py` | 4a / 4b | Admin handlers (§2.4, §3.5). |
| `telegram/telethon_adapter.py` | 4b | `TelethonReadAdapter`; the **only** module that imports `telethon`. |
| `telegram/scheduler.py`, `telegram/deadline.py` | 4b | Fair admission, `WorkBudget`, `Deadline`. |
| `telegram/errors.py` | 4b | The two-layer error translation of §6.3. |
| `telegram/search.py` | 4c | Continuation engine and coverage accounting. Pure; takes a fetch callable. |

## 2. Phase 4a — real seams, authenticated ingress, first real sensitive success

### 2.1 Acceptance path

The 4a acceptance path is literally:

```text
HTTP loopback → transport guards → verify_lease → PrincipalContext
  → dispatch validation → SensitiveDispatcher → coordinator
  → MetadataReadAdapter → committed and anchored DisclosureOutcome → MCP response
```

### 2.2 Ingress and identity

`runtime/ingress.py` binds loopback only and runs the shared transport guards
before anything else. `verify_lease` authenticates the local MCP client and
resolves an immutable `PrincipalContext`:

```python
@dataclass(frozen=True)
class PrincipalContext:
    account_id: int
    account_ref: str
    principal_id: int
    principal_ref: str
    client_id: int
    client_ref: str
```

No grants, memberships, epochs or egress levels. Those are authority state:
`snapshot_authority` reads them after argument validation, and
`revalidate_authority` reads them again before disclosure.

A missing, malformed, oversize, expired, future-dated, wrong-`sec` or revoked
bearer produces the same frozen non-enumerating authentication failure **before
tool dispatch**. MCP-declared client name/version never selects identity
(§10.5).

### 2.3 The seams

**`CoordinatorAuthority`**

| Method | Behaviour |
|---|---|
| `freeze_arguments(tool, args)` | Validates once and freezes the typed arguments. Produces `FrozenRequest(canonical_bytes, canonical_request_hmac, validated_args)`: TG-JCS-v1 bytes of the tool-specific canonical request and its keyed HMAC. **No nonce.** |
| `snapshot(tool, request)` | Builds a fresh `AuthorityView` and runs `policy.evaluate`. A `Denial` refuses before any prompt with its frozen code. Produces epochs, `project_scope_digest` and the effective egress ceiling. |
| `worst_case_buckets(tool, snapshot)` | A per-tool estimator (§2.5) giving a demonstrable upper bound. |
| `revalidate(snapshot)` | Re-reads lock/security epoch, client enabled state, policy epoch, selected project epochs and grants, and the authorisation and membership of every peer in the result (§23.7). Returns the frozen code of the first dimension that moved, or `None`. |
| `apply_egress(raw, snapshot)` | Calls the existing `disclosure/egress.py`. |

**`CoordinatorConsent`** wraps the Phase-2 broker. `issue` builds the challenge
from the frozen request (canonical request HMAC, epochs, scope digest,
exposure-snapshot digest, `display_digest`) and **creates the random 128-bit
nonce and the expiry itself**. `consume` waits on the real agent within the
45-second consent window; a timeout returns `CONSENT_UNAVAILABLE`, an explicit
denial `CONSENT_DENIED`. The approval carries the nonce back.

**Phase-3 correction.** `coordinator.py` step 6 changes from
`request_nonce=request.nonce` to `request_nonce=approval.nonce`. A regression
test asserts the reservation's nonce equals the challenge's nonce and differs
across two consents for byte-identical arguments.

### 2.4 Admin handlers needed without Telegram

No handler in `src/` today writes `projects`, `client_projects` or
`project_peers`; the command names are registered but return
`NOT_AVAILABLE_IN_PHASE`. Without handlers, the 4a "real" success would rest on
test-seeded rows. 4a therefore implements:

`project create`, `project list`, `project enable`, `project disable`,
`project grant-client`, `project set-egress`, `project revoke-client`,
`scope mode`.

Each one keeps the router's existing presence gating and bumps the right epoch
in the same transaction, following C2/C4 and the Phase-2 overlap rules.
Enable/disable bumps `project_epoch`; any grant change invalidates cursors
through `project_scope_digest`.

### 2.5 Worst-case estimation

Roadmap decision 5: a conservative ceiling derived from each tool's contract
record bound and the 65,536-byte response cap, reserved globally and for every
potentially contributing selected project. For `list_projects` and
`resolve_project` the record bound is the number of enabled projects visible to
the client, capped by the contract limit. A unit test asserts, for each tool,
`actual ≤ estimate` over the fixture corpus. A shortfall at commit is already a
refusal (`PROOF_GENERATION_FAILED`, Phase 3).

### 2.6 `MetadataReadAdapter`

It implements `list_projects` and `resolve_project` from the SQLite project
tables and returns domain dataclasses. It is named for what it is: two reviewed
capabilities, with no access to Telethon. `RetrievalAdapter.retrieve` dispatches
to typed methods over a closed mapping from tool name to method. There is no
`getattr` on a caller-supplied name.

### 2.7 Exit evidence

Positive:
- `list_projects` and `resolve_project` succeed through the ingress with a real
  `tgml1` bearer, producing a receipt the independent verifier accepts, the
  ledger rows, exactly one audit event and a refreshed anchor. This runs in the
  smoke against the real broker.
- A platform-gated test approves the same call with Touch ID on the paired agent.

Negative, each an automated test:
- Missing, malformed, expired, revoked and wrong-security-epoch bearers all give
  the same frozen failure, and none reaches `dispatch`.
- A valid bearer for client A cannot obtain client B's grants or projects.
- A non-loopback source is rejected.
- Malformed JSON, duplicate keys and oversize bodies never reach the dispatcher.
- Cancellation during consent never reaches `retrieve`, and the reservation is
  released.
- The demo server has no route that can reach `SensitiveDispatcher`.
- A grant revoked while the prompt is open refuses at step 8.

The other seven sensitive tools still return `POLICY_UNCONFIGURED`.

## 3. Phase 4b — Telethon adapter, login, Test DC, four Telegram tools

### 3.1 The adapter

`TelethonReadAdapter` is the only importer of `telethon`. It constructs exactly
one client per account, as §36 requires:

```python
TelegramClient(
    session, api_id, api_hash,
    receive_updates=False,
    request_retries=0,
    flood_sleep_threshold=0,
    raise_last_call_error=True,
)
```

The client is opened only after the per-account session lock is held, with the
session directory at `0700` and its files at `0600`. Startup and shutdown are
serialised, and disconnect is clean.

**Entity resolution never touches the network with caller input.** Every
`InputPeer` is built from the stored canonical `(peer_type, peer_id)` and the
access hash already in the session's entity cache. A cache miss is
`NOT_ACCESSIBLE`. It never falls back to `get_entity`, `contacts.ResolveUsername`,
`users.GetUsers` or `channels.GetChannels`. The runtime recorder (§6.2) would
catch such a fallback if Telethon attempted one.

### 3.2 Deadlines and work

Every adapter call takes an explicit `Deadline`, and every RPC runs inside
`asyncio.timeout(deadline.remaining())`. Nothing is unbounded:

| Caller | Deadline |
|---|---|
| MCP retrieval (coordinator step 7) | the remainder of `telegram_execution_deadline_seconds` (15 s), started at step 7 |
| Admin operation (`auth login`, `scope discover`) | its own bounded operation deadline, a code constant per command |

A `WorkBudget` per call enforces `max_telegram_rpcs_per_call=20`, plus the
search page and hit bounds in 4c. Admission is fair: at most 4 Telegram calls
in total and 2 per client, with a shared FIFO queue across clients, so one
client cannot hold all four slots while another has work queued (§28). Rate
limits (§28 per-minute table) apply per authenticated client, with an
owner-wide ceiling.

### 3.3 Reviewed RPC allowlist (4b)

The allowlist is a list of fully qualified Telethon 1.45.0 request classes, and
each entry is classified for side effects in
`docs/verification/telegram-rpc-review.md`, citing the current Telegram method
page, before it merges.

| Class | Use |
|---|---|
| `telethon.tl.functions.messages.GetDialogsRequest` | `list_chats`, `get_unread`, discovery |
| `telethon.tl.functions.messages.GetPeerDialogsRequest` | unread and read-marker refresh for known peers |
| `telethon.tl.functions.messages.GetHistoryRequest` | `get_messages` |
| `telethon.tl.functions.messages.GetMessagesRequest` | by-ID, private chats and basic groups |
| `telethon.tl.functions.channels.GetMessagesRequest` | by-ID, supergroups and channels |
| `telethon.tl.functions.users.GetUsersRequest` | `auth status` (`InputUserSelf` only) |

The login set, sent by Telethon's own `send_code_request`, `sign_in` and 2FA
helpers (read from the pinned 1.45.0 source, `telethon/client/auth.py`):

| Class | Use |
|---|---|
| `telethon.tl.functions.auth.SendCodeRequest` | request the login code |
| `telethon.tl.functions.auth.SignInRequest` | submit the code |
| `telethon.tl.functions.account.GetPasswordRequest` | fetch 2FA SRP parameters |
| `telethon.tl.functions.auth.CheckPasswordRequest` | submit the 2FA proof |
| `telethon.tl.functions.updates.GetStateRequest` | Telethon's post-login state fetch |

Connection plumbing, issued by Telethon itself (`telegrambaseclient.py`,
`mtprotosender.py`): `InvokeWithLayerRequest`, `InitConnectionRequest`,
`InvokeWithoutUpdatesRequest`, `help.GetConfigRequest`, `PingRequest`, and
`auth.ExportAuthorizationRequest`/`auth.ImportAuthorizationRequest` for DC
migration. These are allowlisted in the runtime recorder as transport. Our
source never constructs them.

Explicitly **not** allowlisted, and asserted absent: `auth.ResendCodeRequest`,
`auth.LogOutRequest` (Phase 5), `account.UpdatePasswordSettingsRequest`,
`account.ConfirmPasswordEmailRequest`, `updates.GetDifferenceRequest`, every
takeout request, `messages.SearchGlobalRequest`, and everything in the §37
prohibited list.

### 3.4 The four tools

**`list_chats`**
- Calls `GetDialogs` and filters by project membership and owner scope *before*
  any `peer_ref` is minted (§17.4).
- The cursor state is `(anchor_date, anchor_id, anchor_peer_ref,
  next_offset_date, next_offset_id, offset_peer_ref, seen_ids)`, because
  `messages.getDialogs` pages on all three of `offset_date`, `offset_id` and
  `offset_peer`.
- Duplicates are de-duplicable by `peer_ref`.

**`resolve_peer`**
- Matches over the **authorised peer cache only**: exact display name, then
  exact username, then prefix, then substring.
- No `get_entity(query)`, no phone numbers, invite links, `t.me` URLs or foreign
  usernames, and no fuzzy or embedding matching.
- `ambiguous=true` whenever more than one plausible candidate remains.

**`get_messages`**
- Calls `GetHistory`. The first page records the top message ID and date as
  the anchor, and later pages continue at or below it (§19.5).
- `sender_peer_ref` is set only when the sender's own dialog is currently
  readable inside the selected project; otherwise it is `null` (§19.4).
- Bodies exist only in request/response memory.

**`get_unread`**
- Counts come from dialog fields only; no read-acknowledge method exists on the
  adapter.
- `total_is_exact=true` only when the complete authorised dialog universe was
  evaluated within budget. Otherwise `total_unread_visible=null`,
  `total_is_exact=false` and `meta.partial=true`.
- Paging never silently truncates at the per-page limit.

Cursors reuse `authority/cursors.py`. 4b adds `anchor_peer_ref` to
`ALLOWED_STATE_KEYS` in code; no schema change.

### 3.5 Operator plane over admin IPC

The CLI never opens the Telethon session; every Telegram RPC runs in the daemon
(§9.2, §9.4).

| Command | Behaviour |
|---|---|
| `auth login` | Phone, code and 2FA password travel over the admin socket. Each RPC step requires a user-presence admin approval first. The values are held only for that step and never logged. |
| `auth status` | `users.GetUsers(InputUserSelf)`; returns authorised or unauthorised with no PII. |
| `auth logout-local` | Stops the client and removes only the local session after presence approval. Never calls `log_out()`. |
| `auth revoke-this-session` | Returns `NOT_AVAILABLE_IN_PHASE`. The runbook tells the operator to revoke from an official Telegram client until Phase 5. |
| `scope discover` | Presence-gated. Enumerates dialogs and mints `tgl_` selection handles bound to a random snapshot ID and the canonical identity. Handles are held in daemon memory only, expire after 5 minutes, and are invalidated on restart or a policy-epoch change (§10.2). |
| `scope allow` / `scope deny` | Presence-gated; consume a valid `tgl_` handle; bump `policy_epoch` in the same transaction. |
| `project add-peer` | Consumes a `tgl_` handle into `project_peers`, with the C4 overlap rules. |

The bootstrap order is: `auth login → scope discover → scope allow →
project create / grant-client (4a) → project add-peer → list_chats`.

`api_hash` is read at daemon start from the login-Keychain generic-password
item (D2). The item name and `api_id` are non-secret configuration. A missing
item leaves the daemon in `AUTH_REQUIRED`; it never prompts through MCP.

### 3.6 Test DC harness

The harness lives under `tests/telegram/` and runs only with
`--run-telegram-testdc`. It uses three throwaway Test DC accounts: owner `A` and
counterparts `B` and `C`, each on a fresh test-server session.

- **Conventions are verified, not frozen.** The Test DC address, the
  `99966XYYYY` numbering and the code rule are checked against the current
  Telethon test-server documentation when the 4b plan is written, and recorded
  there with the date. Telethon warns they can change.
- **`tests/telegram/fixture_builder.py` is the only place write RPCs may
  appear.** It seeds a DM, a basic group, a supergroup, a forum and a channel
  with Unicode, bidi, injection payloads, deletions and renames. A guard asserts
  `src/` never imports it.

**Independent read-state witness (§38.3), canonical form:**
1. `B` sends messages to `A` in a private DM.
2. A separate process on `B`'s session records `B`'s `read_outbox_max_id` for
   that dialog.
3. `A` calls every 4b read tool that can touch the chat.
4. `B` reads its `read_outbox_max_id` again, and it must be unchanged.

Telegram reports this marker from the recipient's side, so the gateway process
cannot forge it. Groups and channels are covered by the source AST prohibition
and the runtime recorder, not by stretching this field beyond what it means.

### 3.7 Exit evidence

- The read-state witness holds.
- The runtime recorder shows zero requests outside §3.3 across the whole Test
  DC run.
- Deleted and renamed peers behave per spec.
- Unicode and bidi display names round-trip.
- A group message whose sender's DM is outside the project yields a null
  `sender_peer_ref`.
- Pagination under concurrent sends: a page anchor is never shifted, and
  duplicates are removable by ref.
- Flood-wait, when observed, returns `FLOOD_WAIT` with no sleep.

## 4. Phase 4c — context, both searches, coverage, qualification

### 4.1 `get_context`

`message_ref` resolves through `message_refs` to `(canonical peer, telegram
message id)`. Before any RPC, the peer must pass *current* owner scope and
membership in the supplied project, so a historical ref cannot get around a
later deny (§20.4).

1. **Anchor fetch.** Call `messages.GetMessages` or `channels.GetMessages` by
   ID. This proves the anchor exists (`MESSAGE_NOT_FOUND` if not) and yields its
   topic root: `reply_to.reply_to_top_id`, or the message itself when it is a
   topic root. `message_refs` stays unchanged; no topic column is added.
2. **Window, ordinary chat.** `messages.GetHistory` with `offset_id=anchor_id`,
   `add_offset=-(after+1)`, `limit=before+after+1`.
3. **Window, forum topic.** `messages.GetReplies` with `msg_id=topic_root_id`
   and the same offset/add_offset/limit. It never crosses into another topic.
4. **Anchor is the root.** The root came back from step 1; `getReplies` supplies
   the messages after it. Nothing can precede a root inside its topic.
5. **Trim.** Assert the anchor is present, then trim deterministically to
   `before` older and `after` newer messages, never more than 101 in total.

That is at most two RPCs per call. **To verify when the 4c plan is written:**
whether Telegram requires `limit > -add_offset` strictly. If it does, `before=0`
breaks the formula, so fetch `before+after+2` and let step 5 trim. The 101 cap is
enforced after the trim, before egress.

`messages.GetRepliesRequest` joins the reviewed allowlist, with its
side-effect classification.

### 4.2 `search_messages`

- Runs a per-peer `messages.SearchRequest` over exactly the selected project's
  owner-authorised peers, optionally narrowed to one `peer_ref` that must itself
  pass membership and scope.
- `messages.SearchGlobalRequest` stays forbidden by the AST guard and the
  runtime recorder (§21.4).
- **Boundaries.** Telegram's `min_date` and `max_date` are both strict. So the
  RPC uses `rpc_min_date = floor(since) - 1s` and `rpc_max_date = ceil(until) +
  1s` (each clamped), and returned records are post-filtered to
  `since ≤ sent_at < until`. Over-fetching is harmless; under-fetching is the
  bug.
- Query text is never persisted; the cursor holds only the keyed query HMAC.

### 4.3 The continuation model

Roadmap item 7 required this to be defined before any continuation code.

- **Upper anchor.** The first page fixes
  `upper = min(until, first_request_time)` (or `first_request_time` when `until`
  is null), and every continuation keeps it, so new messages cannot shift the
  universe.
- **Peer universe.** Eligible canonical peers are sorted by
  `(peer_type, peer_id)`. The cursor stores `universe_digest`, an HMAC of the
  full ordered universe. It never stores the list.
- **Window.** At most 64 active peers at a time, the existing reviewed
  `_PER_PEER_MAX` in `authority/cursors.py`, inside the existing 8,192-byte
  state cap. The cursor stores `window_start`, `next_unstarted_index` (both
  indexes into the full universe), and for each active peer `(offset_id,
  exhausted)` keyed by `peer_ref`.
- **Window invariant.** Every peer below `window_start` is exhausted. The
  window advances only past exhausted peers, so a continuation never skips a
  peer.
- **A page** round-robins across the non-exhausted active peers until `limit`
  hits are held or a work bound fires.
- **Page ordering.** Each emitted page is sorted newest-first by `sent_at`, then
  `message_ref`, over the hits examined for that page. When
  `coverage.complete=false`, the gateway does not claim those hits are the
  globally newest across peers not yet exhaustively examined. **A global
  newest-first ranking is authoritative only for an exhaustive result set**,
  meaning `coverage.complete=true`, whether it was reached in one page or
  through continuation.
- **New state keys** are added to `ALLOWED_STATE_KEYS` in code:
  `upper_date`, `universe_digest`, `window_start`, `next_unstarted_index`.

**Cursor check order.** The frozen error hierarchy runs first; the digest is an
integrity check after it:

```text
security_epoch        → INVALID_CURSOR (no revival after unlock)
policy_epoch          → CURSOR_POLICY_CHANGED
project_scope_digest  → CURSOR_PROJECT_CHANGED
universe_digest       → CURSOR_PROJECT_CHANGED (membership moved without an epoch-visible cause)
```

A peer-universe change caused by owner policy therefore reports
`CURSOR_POLICY_CHANGED`, never the digest error.

### 4.4 Coverage

Emitted exactly as §23D requires.

- `complete=true` only if every eligible peer is exhausted and no deadline,
  RPC, hit, peer or response bound fired. That forces `next_cursor=null`,
  `meta.partial=false` and empty `partial_reasons`.
- **Every non-null `next_cursor` requires `response_limit` in
  `partial_reasons`**, even when another bound also fired
  (e.g. `["rpc_budget", "response_limit"]`).
- `telegram_partial` covers any Telegram response whose own metadata makes
  completeness unknowable, such as an `inexact` slice. Telegram uncertainty is
  never reported as `complete=true`.
- More than 250 eligible peers gives a bounded partial result with
  `peer_budget` (§23D permits this over rejection), continued through the
  windowing above.
- Shared peers are counted once in the global `eligible_peers`/`peers_scanned`
  and once in each contributing project's `project_coverage` entry.
- Every selected project appears in `project_coverage[]`, including with
  `peers_scanned=0`.

### 4.5 `cross_project_search`

- Uses the same engine over the de-duplicated union of each selected project's
  owner-authorised peers; a shared peer is searched once.
- Every selected project must be enabled, with `can_read=1` and
  `can_cross_search=1` for the client, and have its epoch in the consent and
  cursor binding.
- Every hit carries all selected projects the peer currently belongs to in
  `matched_projects`.
- Pages are merged newest-first by `(sent_at, message_ref)`, under the ordering
  claim of §4.3.
- The consent is distinct: it names every project and carries the
  context-insertion warning. Project names and query text are display-only and
  never enter the digested security object (§9.8).
- The cursor binds the sorted project refs, the epoch vector, the client, the
  owner policy epoch and the query HMAC.
- There is no `all_projects` flag and no fallback.

**Race test:** a shared peer's membership is removed after `retrieve` but before
`commit_disclosure`. Revalidation must discard the result, so no stale
`matched_projects` is emitted.

### 4.6 Qualification (end of 4c)

The evidence is split by what each source can honestly prove.

| Evidence | Where |
|---|---|
| Read-state witness, forum isolation, deleted anchor and messages, renamed peers, Unicode and bidi, pagination races, real search exhaustion, zero mutation | Test DC, then re-run on the **dedicated non-primary account** |
| All six `partial_reasons` (`deadline`, `rpc_budget`, `hit_budget`, `peer_budget`, `response_limit`, `telegram_partial`) | Deterministic fault-injection integration tests against `FakeTelegram` |

Forcing `peer_budget` organically would need more than 250 authorised peers on
throwaway accounts. The spec requires the behaviour, and the fault-injection
suite proves it.

## 5. Continuation state-machine tests

The Appendix-L formal model covers the coordinator protocol, and continuation
does not alter that protocol, so the model gains no state. Pagination is tested
as its own state machine: a Hypothesis stateful test over `FakeTelegram`
covering:

- cursor replay within TTL, and expiry;
- policy mutation between pages (`CURSOR_POLICY_CHANGED`);
- project, grant or egress mutation between pages (`CURSOR_PROJECT_CHANGED`);
- the window transition at peer 64 → 65 and at 250 → 251;
- hits deleted between pages;
- new hits arriving above the frozen upper anchor, which never appear;
- duplicate suppression by ref;
- the `response_limit` invariant on every non-null cursor;
- **no eligible peer is ever skipped across a full continuation run.** The union
  of peers scanned over all pages equals the universe.

## 6. Cross-cutting

### 6.1 Source guards (§37)

One AST test, reading fully-qualified names so import aliases resolve, asserts:

1. Only `telegram/telethon_adapter.py` imports anything from `telethon`.
2. Every `telethon.tl.functions.*` reference in `src/` is in the reviewed
   allowlist for the current sub-phase: none in 4a, §3.3 in 4b, plus
   `messages.SearchRequest` and `messages.GetRepliesRequest` in 4c.
3. The adapter's exported callables match the reviewed `TelegramReadService`
   surface by **name and signature**.
4. No §37 prohibited symbol appears in `src/`.
5. `dispatch`, `sensitive_dispatch` and `tools/*` import no concrete adapter
   (`telegram.metadata`, `telegram.telethon_adapter`); only
   `runtime/composition.py` does.
6. `src/` never imports `tests/telegram/fixture_builder.py`.

### 6.2 Runtime outbound-RPC recorder

Source AST shows what our code can construct directly. It cannot see what
Telethon's own helpers send: `sign_in()` issues TL requests the AST never sees.
So the adapter's integration and Test DC tests install a recorder at Telethon's
sender boundary, and **every request class actually submitted must be in the
reviewed allowlist plus the transport set of §3.3**. The two controls are
independent: source AST covers what our code can construct, the runtime trace
covers what Telethon actually sends. A recorded class outside the list fails the
run.

The recorder lives in `tests/` and is injected. It is not a flag inside the
adapter.

### 6.3 Error translation, in two layers

Telethon RPC errors are matched most specific first, because Telethon 1.45
can surface an unknown Telegram error only as a base `RPCError` subclass:

```text
FloodWaitError (and subclasses)   → FLOOD_WAIT  (+ retry_after_seconds, no sleep)
reviewed invalid/revoked-auth set → SESSION_REVOKED   (latches the adapter)
unauthorised session              → AUTH_REQUIRED
reviewed access-denied set        → NOT_ACCESSIBLE
temporary server / network        → TELEGRAM_UNAVAILABLE
any other RPCError                → INTERNAL_ERROR   (sanitised; class name logged, no message)
```

Gateway control flow is separate, because these are not Telegram errors:

```text
asyncio.timeout expiry            → DEADLINE_EXCEEDED
WorkBudget exhaustion             → WORK_BUDGET_EXCEEDED
authorised but missing message    → MESSAGE_NOT_FOUND
caller cancellation               → propagated; never caught
```

`asyncio.CancelledError` is never caught by any catch-all. It unwinds through
the Phase-3 reservation release and fail-closed path, and no partial payload is
emitted. A test asserts this for cancellation at retrieval, at each RPC inside
a multi-RPC call, and during consent. A Telegram failure after reservation
releases the reservation, as Phase 3 already does.

### 6.4 Content-leak gauntlet

Each Test DC run generates a fresh high-entropy marker at runtime, sends it
through the fixture builder, calls the tools, and then scans every
gateway-owned persistence and diagnostic surface:

- the metadata DB and its `-wal` and `-shm` files;
- cursor rows;
- disclosure receipts, the exposure ledger, and audit events and checkpoints;
- settings;
- the Telethon session directory;
- gateway logs;
- crash, debug and trace artifacts, whenever the test enables them.

The claim is precisely: **no fixture message-body marker persists in any
gateway-owned prohibited persistence or diagnostic surface.** This extends the
Phase-3 whole-store sweep (receipts, ledger, audit, checkpoints, settings, logs)
and does not replace it. A control run plants the marker deliberately to prove
the scan can fail.

### 6.5 Test layers

| Layer | Runs | Proves |
|---|---|---|
| Unit + integration against `FakeTelegram` | always | Every behaviour above, including all fault injection. `FakeTelegram` implements the same domain protocol as `TelethonReadAdapter`. |
| Pagination state machine (§5) | always | Continuation correctness. |
| Smoke (`scripts/e2e_smoke.py`) | always | 4a: real ingress → real receipt. 4b and 4c: the same path with `FakeTelegram` injected. The smoke never touches the network. |
| `--run-telegram-testdc` | opt-in | Real transport, read state, exhaustion, recorder, leak sweep. |
| `--run-platform-gated` | opt-in | Touch ID approval for a real sensitive success. |
| Formal model (`tests/formal`) | always | Unchanged, and must stay green. |

### 6.6 Audit trail and the green gate

Each sub-phase has its own plan (`phase-4a`, `phase-4b`, `phase-4c`), its own
dated `**Raouf:**` entries in AGENT.md and CHANGELOG.md, and its own section in
`docs/verification/phase-4.md`.

**A sub-phase may commit only when its own acceptance suite and every
Phase-4-owned applicable check are green.** Release gates whose remaining
evidence is owned by Phase 5, 6 or 7 stay PARTIAL, naming what is missing.
Nothing red is ever painted green, and no pipeline exit status is masked.

Order is strict: 4a is green before any Telethon code exists, and 4b is green
before any search code exists. 4c ends with the dedicated-account run.

## 7. Claim boundaries

- **Read-only** is proven for the reviewed RPC set, the recorded runtime trace
  and the DM read-marker witness. It is not a proof that MTProto does no session
  housekeeping (§9.3).
- **Coverage `complete=true`** means every eligible peer reached Telegram's
  no-more-results condition within bounds. It does not attest message-body
  integrity or remote delivery (roadmap decision 8).
- **Test DC and dedicated-account evidence** is not evidence for the installed
  Codex or Claude builds or the tunnel. That belongs to Phase 6.

## 8. Out of scope

- `auth revoke-this-session` and `LogOutRequest`, key and credential rotation,
  retention, backup and restore, and policy explain/simulate/diff (Phase 5).
- Installed clients, the tunnel and mTLS ingress (Phase 6).
- Release artifacts, SBOM, signed evidence (Phase 7).
- Media download, URL fetch, embeddings, any write or read-acknowledge path:
  never, in V0.1.10.

## Normative grounding

§8.2.1, §8.4 (ingress, demo); §9.1–§9.4, §9.8 (credentials, bootstrap, session,
consent); §10.2–§10.5 (discovery, identity); §12.2 (`message_refs`, `cursors`);
§13.1 (time); §17–§22, §21A (tools); §23–§23E (cursors, disclosure, coverage);
§27–§28 (errors, limits); §35–§37 (service interface, adapter rules, guards);
§38.3, §38.15 (read-state, Test DC); Appendix L (formal model); roadmap
decisions 5, 7, 8 and the Phase 4 row; Phase-3 design §2 (twelve steps, two
barriers).
