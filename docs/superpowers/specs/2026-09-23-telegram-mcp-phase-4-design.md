# Telegram MCP Phase 4 Design — Allowlisted Telegram Adapter and Vertical Tool Slices

**Status:** Revision 3 (§3.8 added: live admin approvals, identity bootstrap, 4a follow-ups carried into 4b). Revision 2: Four design sections were presented and reviewed one at
a time, and every amendment was folded in. Revision 2 then gauntleted revision 1
against the shipped code, the pinned Telethon 1.45.0 source and Telegram's
method pages, and found sixteen defects. They are listed in §0A and fixed in
place.
**Date:** 2026-09-23 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`)
**Roadmap:** [release roadmap](../plans/2026-09-22-telegram-mcp-release-roadmap.md), Phase 4 row
**Basis:** Phases 1–3 complete on `main` at `1efc633`; verified fresh for this
design — `626 passed, 7 skipped`.

## Controlling statement

**Phase 4 does not change the public MCP tool catalogue, the schema or any
frozen wire.** The ten contracts, the nineteen §12.2 tables (`schema_version`
included), TG-JCS-v1, the challenge
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
| D9 | `cross_project_search` truncates an eligible universe larger than `max_cross_project_peers` to its first 250 canonical peers. `peer_budget` then appears on every page, and `complete` is never true. Continuation never goes past the cap. | CT-139: "peer-cap exhaustion cannot set `complete=true`". Revision 1 windowed past 250, which could eventually report complete over a universe larger than the cap. |

## 0A. What revision 2 changed

Each row was verified by executing or reading the named artifact, not inferred.

| # | Defect in revision 1 | Evidence | Fix |
|---|---|---|---|
| G1 | The daemon has no prompt-delivery path. `PROMPT`/`APPROVAL`/`DENIAL` frames are spoken only by `tests/agent/stub_broker.py`; the only `broker.consume` callers are `doctor.py` self-tests. "`consume` waits on the real agent" named something that does not exist. | `grep -rln PROMPT src` → nothing | New §2.4, `consent/prompter.py`. |
| G2 | `ConsentBroker.issue` is `async`, but the coordinator calls `self._consent.issue(...)` without `await`. | `broker.py:178`, `coordinator.py:303` | The coordinator awaits `issue` (§2.3). |
| G3 | `ConsumedChallenge` carries neither the nonce (needed for D5) nor the exposure-snapshot digest (needed for `snapshot_matches`). "The approval carries the nonce back" was false. | `broker.py:78-90` | Both fields are added to the internal dataclass (§2.3). |
| G4 | Consent timeout was mapped to `CONSENT_UNAVAILABLE`. §27.1 defines `CONSENT_DENIED` as "denied/cancelled/timed out", and the shipped broker maps `challenge-expired` to `DEADLINE_EXCEEDED`, which is also wrong. | §27.1; `broker.py:68-74` | Timeout maps to `CONSENT_DENIED`, and the broker mapping is corrected (§2.3). |
| G5 | "Keeps the router's existing presence gating": the router gates only `lock` and `unlock`. | `admin.py:130` | Explicit presence set per sub-phase (§2.5, §3.5). |
| G6 | Cursor state: `exhausted` is not an allowed key and booleans are rejected. `universe_digest` and `anchor_peer_ref` would fail the validator, which accepts only integers, `*_date` strings and `offset_peer_ref`. "Add keys to `ALLOWED_STATE_KEYS`" understated the change. | `cursors.py:337-364` | The frontier holds only non-exhausted peers, and the validator changes are named (§4.3). |
| G7 | "A Hypothesis stateful test": Hypothesis is not a dependency, and §7.2 requires review of any new one. | `pyproject.toml`, `uv.lock` | Exhaustive seeded enumeration in the style of `formal/`; no new dependency (§5). |
| G8 | The topic root was read from `reply_to.reply_to_top_id`. Per Telegram, a post directly in a topic has `forum_topic=true` and the topic ID in `reply_to_msg_id`, with no `reply_to_top_id`. General-topic messages (`id=1`) carry no `forum_topic` at all. | `constructor/messageReplyHeader`, `api/forum` | Three-case topic rule plus a General-topic path (§4.1). |
| G9 | `updates.GetDifferenceRequest` was "asserted absent", but Telethon's `_on_login` sends `GetState` then `GetDifference` unconditionally after sign-in, whatever `receive_updates` is. The recorder would have failed every login. | `telethon/client/auth.py:394-396` | Phase-scoped recorder allowlist (§3.3, §6.2). |
| G10 | The ingress failure was "the same frozen failure" without saying what it is. §8.2.1 requires authentication before schema parsing, so it cannot be an MCP tool result. | §8.2.1 | HTTP 401 with a fixed body before JSON-RPC parsing (§2.2). |
| G11 | §28 rate limits apply, but no code was named, and §27.1 has none for them. Inventing one would break the frozen enum. | §27.1, §28 | HTTP 429 with `Retry-After` before dispatch (§3.2). |
| G12 | More than 250 peers continued through windows (see D9). | CT-139, §23D | D9. |
| G13 | The 250 peer bound was applied to ordinary search too. `max_cross_project_peers` is a cross-project limit; ordinary search is bounded only per call (§13.2, §21.5). | §28, §21.5 | Scoped to cross-project (§4.4). |
| G14 | The dev Keychain placement was presented as §9.1-compliant. A login-Keychain item is reachable, via the Keychain ACL, by other processes of the same user, including the coding agent. It is not a daemon-only store. | §9.1 | Recorded as a named deviation (§3.5, §7). |
| G15 | Search bounds: Telegram treats `min_date`/`max_date` of 0 as "no bound", so a clamp that reaches 0 would silently widen the search. | `method/messages.search` | Clamp to at least 1, and omit the bound when absent (§4.2). |
| G16 | "Eighteen tables": the migration creates nineteen, `schema_version` included. | `grep CREATE TABLE migrations.py` | Controlling statement corrected. |

Checked and **not** a defect: every Telethon constructor argument in §3.1 exists
in 1.45.0; all seven named request classes import; `messages.search` bounds are
strict and `messagesSlice.inexact` exists; `messages.getReplies` takes the
offset/add_offset/limit triple. Telegram's pagination page gives the
around-message example (`offset_id=MSGID, add_offset=-10, limit=20`) and states
no strict `limit > -add_offset` rule, so §4.1 keeps that as an empirical Test DC
check rather than an assumption.

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
| `consent/prompter.py` | 4a | Daemon half of the `PROMPT`/`APPROVAL`/`DENIAL` wire over the live RV-1 session (§2.4). |
| `telegram/service.py` | 4a | `TelegramReadService` protocol and domain dataclasses (§35). No Telethon. |
| `telegram/metadata.py` | 4a | `MetadataReadAdapter`: `list_projects`, `resolve_project` from SQLite. |
| `ipc/handlers/projects.py`, `ipc/handlers/scope.py` | 4a / 4b | Admin handlers (§2.5, §3.5). |
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
bearer produces **HTTP 401** with one fixed body and no JSON-RPC envelope,
**before the request body is parsed**. §8.2.1 requires authentication before
schema parsing, so the failure cannot be an MCP tool result. Every cause gets the
same status and bytes, so the response does not say which check failed (§14.3).
A client disabled *during* a request is a different case: it surfaces at step 8
as `CLIENT_REVOKED` (§23.7). MCP-declared client name/version never selects
identity (§10.5). The ingress keeps the SDK's DNS-rebinding protection with
exact `allowed_hosts` (§29.3).

### 2.3 The seams

**`CoordinatorAuthority`**

| Method | Behaviour |
|---|---|
| `freeze_arguments(tool, args)` | Validates once and freezes the typed arguments. Produces `FrozenRequest(canonical_bytes, canonical_request_hmac, validated_args)`: TG-JCS-v1 bytes of the tool-specific canonical request and its keyed HMAC. **No nonce.** |
| `snapshot(tool, request)` | Builds a fresh `AuthorityView` and runs `policy.evaluate`. A `Denial` refuses before any prompt with its frozen code. Produces epochs, `project_scope_digest` and the effective egress ceiling. |
| `worst_case_buckets(tool, snapshot)` | A per-tool estimator (§2.6) giving a demonstrable upper bound. |
| `revalidate(snapshot)` | Re-reads lock/security epoch, client enabled state, policy epoch, selected project epochs and grants, and the authorisation and membership of every peer in the result (§23.7). Returns the frozen code of the first dimension that moved, or `None`. |
| `apply_egress(raw, snapshot)` | Calls the existing `disclosure/egress.py`. |

**`CoordinatorConsent`** wraps the Phase-2 broker.
- `issue` is `async`, because `ConsentBroker.issue` is. It builds the challenge
  from the frozen request (canonical request HMAC, epochs, scope digest,
  exposure-snapshot digest, `display_digest`), and `canonical_challenge` creates
  the random 128-bit nonce and the expiry.
- `consume` hands the handle to the prompter (§2.4) and awaits the agent's
  envelope within the 45-second window, then calls `broker.consume(handle,
  envelope)`.
- Outcomes follow §27.1: an explicit denial, a cancellation or a **timeout**
  gives `CONSENT_DENIED`; no paired agent connected, or the broker's consent
  rate limit (5/minute, 30/hour, §9.8), gives `CONSENT_UNAVAILABLE`.

**Phase-2 and Phase-3 corrections, each with a regression test:**
- `coordinator.py` step 4 becomes `await self._consent.issue(...)`.
- `ConsumedChallenge` gains `nonce` and `exposure_snapshot_digest`. It is an
  in-process dataclass, not a wire; the signed challenge bytes are unchanged.
- The coordinator's step-6 reservation uses `request_nonce=approval.nonce`, not
  `request.nonce`. The test asserts the reservation's nonce equals the
  challenge's and differs across two consents for byte-identical arguments.
- `snapshot_matches` compares `approval.exposure_snapshot_digest` with the
  digest re-derived under the reservation lock.
- `ConsentError.dispatch_code` maps `challenge-expired` to `CONSENT_DENIED`, not
  `DEADLINE_EXCEEDED`.

### 2.4 Prompt delivery (`consent/prompter.py`)

Nothing in `src/` sends a prompt today. The Phase-2 join gate drives the real
agent through `tests/agent/stub_broker.py`, which speaks the frames itself. 4a
adds the daemon half:

- It holds the one live RV-1 session (`ipc/rendezvous.serve_rendezvous`) with
  the paired agent, which is authenticated by its transport key.
- `prompt(handle)` writes a `PROMPT` frame carrying the broker's
  `challenge_bytes` and `daemon_signature`, then awaits exactly one `APPROVAL`
  or `DENIAL` frame for that handle.
- Frame encoding and decoding use `ipc/framing.py`, the one frame codec. The
  stub broker is changed to import it, so the tests stop carrying a second copy.
- With no live session, it returns `CONSENT_UNAVAILABLE` at once, never after
  the 45 seconds.
- Agent death mid-prompt invalidates the pending challenge
  (`broker.invalidate`) and gives `CONSENT_DENIED`. Caller cancellation
  invalidates it and propagates.
- One prompt is in flight per session. Other requests queue behind it inside
  their own 45-second windows, never sharing an approval (§9.8, one call per
  signature).

The join gate (`tests/integration/test_join_gate.py`) gains a case that drives
the prompter itself against the packaged agent, so both halves of the frozen
prompt-frame wire are proven by shipped code.

### 2.5 Admin handlers needed without Telegram

No handler in `src/` today writes `projects`, `client_projects` or
`project_peers`; the command names are registered but return
`NOT_AVAILABLE_IN_PHASE`. Without handlers, the 4a "real" success would rest on
test-seeded rows. 4a therefore implements:

`project create`, `project list`, `project enable`, `project disable`,
`project grant-client`, `project set-egress`, `project revoke-client`,
`scope mode`.

**Presence gating is added, not inherited.** Today `PRESENCE_GATED` holds only
`lock` and `unlock` (`admin.py:130`). §33 requires that "mutations remain
separate user-presence-gated commands", so 4a adds every mutating command above
to `PRESENCE_GATED`: all of them except `project list`. A test asserts that the
gated set equals the mutating set in the command table, so a new mutating
command cannot land ungated.

Each handler bumps the right epoch in the same transaction, following C2/C4 and
the Phase-2 overlap rules.
Enable/disable bumps `project_epoch`; any grant change invalidates cursors
through `project_scope_digest`.

### 2.6 Worst-case estimation

Roadmap decision 5: a conservative ceiling derived from each tool's contract
record bound and the 65,536-byte response cap, reserved globally and for every
potentially contributing selected project. For `list_projects` and
`resolve_project` the record bound is the number of enabled projects visible to
the client, capped by the contract limit. A unit test asserts, for each tool,
`actual ≤ estimate` over the fixture corpus. A shortfall at commit is already a
refusal (`PROOF_GENERATION_FAILED`, Phase 3).

### 2.7 `MetadataReadAdapter`

It implements `list_projects` and `resolve_project` from the SQLite project
tables and returns domain dataclasses. It is named for what it is: two reviewed
capabilities, with no access to Telethon. `RetrievalAdapter.retrieve` dispatches
to typed methods over a closed mapping from tool name to method. There is no
`getattr` on a caller-supplied name.

### 2.8 Exit evidence

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
- Consent through the prompter: agent approval releases; agent denial, timeout
  and agent death each give `CONSENT_DENIED` and leave nothing reserved; no
  connected agent gives `CONSENT_UNAVAILABLE` at once; two concurrent calls
  get two prompts and never share an approval.
- Bad bearers and rate-limit breaches are refused at HTTP level (401/429)
  before the body is parsed, byte-identical across causes.

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
client cannot hold all four slots while another has work queued (§28).

**Rate limits** (the §28 per-minute table) apply per authenticated client, with
an owner-wide ceiling, and are checked at the ingress **before dispatch and
before consent**. The frozen §27.1 enum has no rate-limit code, and inventing
one would break every advertised error schema. So an exceeded limit is **HTTP
429** with `Retry-After` and a fixed body, like the 401 in §2.2, and no tool
result. `FLOOD_WAIT` stays reserved for Telegram's own wait (§27.2).

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
| `telethon.tl.functions.updates.GetStateRequest` | sent by Telethon's `_on_login` |
| `telethon.tl.functions.updates.GetDifferenceRequest` | sent by Telethon's `_on_login`, unconditionally |

`_on_login` (`telethon/client/auth.py:394-396`) sends `GetState` and then
`GetDifference` after every successful sign-in, whatever `receive_updates` is
set to. `GetDifference` is read-only: it acknowledges nothing and changes no
read state. It does, however, pull recent updates, including message content,
into process memory during login. So the recorder's allowlist is **scoped by
operation**:

| Recorder phase | Allowed beyond transport |
|---|---|
| `admin.login` (while `auth login` runs) | the login set above |
| `mcp.retrieval` (coordinator step 7) | the retrieval set for the current sub-phase |
| `admin.discover` (while `scope discover` runs) | `messages.GetDialogsRequest` only |

A `GetState` or `GetDifference` outside `admin.login` fails the run. The leak
sweep of §6.4 runs after login too, over the session directory, because Telethon
may cache entities from that difference.

Connection plumbing, issued by Telethon itself (`telegrambaseclient.py`,
`mtprotosender.py`): `InvokeWithLayerRequest`, `InitConnectionRequest`,
`InvokeWithoutUpdatesRequest`, `help.GetConfigRequest`, `PingRequest`, and
`auth.ExportAuthorizationRequest`/`auth.ImportAuthorizationRequest` for DC
migration. The recorder allows these in every phase as transport, and unwraps
`Invoke*` wrappers to record the inner request. Our source never constructs any
of them.

Explicitly **not** allowlisted in any phase, and asserted absent:
`auth.ResendCodeRequest`, `auth.LogOutRequest` (Phase 5),
`account.UpdatePasswordSettingsRequest`, `account.ConfirmPasswordEmailRequest`,
`contacts.ResolveUsernameRequest`, `channels.GetChannelsRequest`, every takeout
request, `messages.SearchGlobalRequest`, and everything in the §37 prohibited
list. `users.GetUsersRequest` is allowed only as `InputUserSelf`.

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

Cursors reuse `authority/cursors.py`, with no schema change. 4b adds
`anchor_peer_ref` to `ALLOWED_STATE_KEYS`, and **the validator learns to check
it as a `tgp_` ref**. Today only `offset_peer_ref` gets ref validation; any
other non-date key must be an integer, so without that change the new key would
be rejected. A test asserts that each allowed key has exactly one value rule.

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

4b adds `auth login`, `auth logout-local`, `scope discover`, `scope allow`,
`scope deny` and `project add-peer` to `PRESENCE_GATED`. `auth status` stays
ungated because it is read-only and returns no PII. The gated-equals-mutating
test from §2.5 covers them. `scope discover` is not a mutation, but §10.2 gates
it explicitly because it exposes private dialog metadata.

The bootstrap order is: `auth login → scope discover → scope allow →
project create / grant-client (4a) → project add-peer → list_chats`.

`api_hash` is read at daemon start from the login-Keychain generic-password
item (D2). The item name and `api_id` are non-secret configuration. A missing
item leaves the daemon in `AUTH_REQUIRED`; it never prompts through MCP.

**Recorded deviation (dev only).** A login-Keychain item is not the daemon-only
store §9.1 requires. Any process running as the same user can request it through
the Keychain ACL, and that includes a coding agent. The deviation is recorded
in `docs/verification/phase-4.md` with its bound: Test DC and the dedicated
non-primary account only, never the primary account's credentials. It closes
when the `telegram-mcpd` service account is installed and the secret moves into
its store.

Test DC and dedicated-account session files live outside the repository under
the daemon's session directory (`0700`/`0600`). The Appendix-B `.gitignore`
patterns already cover `*.session*`, and a test asserts `git check-ignore`
blocks them.

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

### 3.8 Revision 3 additions for 4b (decided 2026-09-23)

**Live admin approvals reuse the frozen challenge wire (owner's decision).**
When a presence-gated admin command arrives (§2.5), the daemon itself
prompts through the same broker and prompter used for MCP consent. The CLI
never supplies a proof. The signed challenge is the unchanged wire:

| Field | Admin value |
|---|---|
| `tool` | `admin.<command>`, e.g. `admin.auth_login_code`. The dotted prefix can never collide with the ten tool names (the audit chain already uses `admin.lock`). |
| `client` | a per-install **operator sentinel** `tcl_` ref, derived as `mint`-shaped base32 of HMAC(privacy-key, "operator"). It is never an `mcp_clients` row, so it can never authenticate an MCP request. |
| `principal` | the owner principal. |
| `account` | the real account, or a per-install **pre-login sentinel** `tga_` ref, derived the same way from "pre-login", before any account exists. |
| `request_hmac` | keyed HMAC of the canonical admin arguments with secrets removed (a phone number, code or password never enters a challenge, display or log). |
| epochs, scope, exposure | current epochs; the list-projects scope digest of the empty set; the synthetic-zero exposure digest (admin actions disclose nothing). |

The display reads `client_display = "Operator (admin socket)"`, the command as
the action, and a fixed risk line. On approval the daemon injects a
one-time, in-memory token as the request's `presence` proof; the router's
existing verifier accepts each token exactly once. No Swift change and no
join-gate change are needed.

**Identity bootstrap.** The owner principal (`local_single_principal`, §10.5)
is created on first daemon start. `auth login` creates the `accounts` row
and the owner's `policy_state` row (`allowlist`, archived excluded, private,
groups and channels included, §10.4). `client rotate --client <kind>`
creates or re-seeds the `mcp_clients` row for `codex_local` or
`claude_code_local` and its lease seed. These end 4a's seeded identity rows.

**Carried from 4a's final review** (`docs/verification/phase-4.md` §7):
`policy.evaluate` gains an explicit owner mode, and under `allowlist` an
empty allowlist denies every peer; display names are rejected at
`project create` if they contain bidi controls, LRM/RLM, U+061C or
U+2028/2029, and the agent's renderer strips the same set; the
coordinator re-checks authority immediately before retrieval as well as at
step 8, so no Telegram RPC runs under authority that moved during consent.

**Budget attribution follows the frozen contracts.** `get_unread` and
`resolve_peer` items cannot carry `origin_project_refs`, so those two tools
charge the client-global bucket only; `list_chats` and `get_messages` carry
the field and charge the project bucket too.

**Topic titles.** `topic_title` stays `null` in 4b: fetching titles needs
`channels.GetForumTopics`, which is not on the reviewed allowlist.
`forum_topic` is set from `reply_to.forum_topic`.

**The chat universe is the project's members (refines §3.4).** `list_chats`,
`get_unread` and `resolve_peer` read exactly the project's readable members
through `messages.GetPeerDialogs` (100 peers per request) instead of paging
`messages.GetDialogs` and filtering. This does three things:

- It is narrower than §17.4. Metadata for chats outside the project never
  enters memory during an MCP call, where §17.4 only requires filtering
  before a ref is minted.
- It pages a fixed, finite set with `seen_ids`, so a concurrent message that
  reorders dialogs can neither duplicate nor skip a chat.
- It makes `total_is_exact` decidable: the total is exact when every readable
  member was found in the entity cache and fetched within budget.

`GetDialogs` remains reviewed, for `scope discover` (`admin.discover`) only. The
cursor state is therefore `seen_ids` for these tools and
`(anchor_id, offset_id)` for `get_messages`, and `anchor_peer_ref` is not
needed.

**Pages fit the response cap.** The sensitive dispatcher refuses a response
over 64 KiB with `RESPONSE_LIMIT`, and it does so after step 11 has committed.
So every page's canonical `data` is fitted under 48 KiB, trailing records
return through the cursor, and the step-3 byte bound is capped at the same
figure. Without the cap, a long full-text page would be charged and receipted
but never delivered, and a default 30-message full-text page would always
prompt as elevated.

**No Telegram tool prompts without a session.** With no usable session, the
four tools refuse at step 2 with `AUTH_REQUIRED` (or `SESSION_REVOKED` after a
revocation), before any consent prompt.

**The adapter owns the wire (revision 4; refines §3.1 and §3.3).** Telethon's
login and status helpers are never used, because in 1.45.0 they retry
(`AuthRestartError`), send the prohibited `auth.ResendCode` when a code hash is
cached, and pull `updates.GetDifference` on login. The adapter's own
`_call` sends each request once, with no retry, sleep, flood cache or hidden
migrate RPC, and only within the current operation's allowlist and work
budget:

- `admin.login`: `auth.SendCode`, `auth.SignIn`, `account.GetPassword`,
  `auth.CheckPassword`, and `help.GetConfig` for one explicit, budgeted DC switch;
- `admin.status`: `updates.GetState`, `users.GetUsers(self)`;
- `admin.discover`: `messages.GetDialogs`;
- `mcp.retrieval`: `messages.GetPeerDialogs`, `messages.GetHistory`,
  `messages.GetMessages`, `channels.GetMessages`.

`updates.GetDifference` is no longer sent in any phase. Admin approvals bind
keyed digests of the secret arguments into the signed request, and each
approval token is bound to its exact request. Retryability comes only from
§27.1.

## 4. Phase 4c — context, both searches, coverage, qualification

### 4.1 `get_context`

`message_ref` resolves through `message_refs` to `(canonical peer, telegram
message id)`. Before any RPC, the peer must pass *current* owner scope and
membership in the supplied project, so a historical ref cannot get around a
later deny (§20.4).

1. **Anchor fetch.** Call `messages.GetMessages` or `channels.GetMessages` by
   ID. This proves the anchor exists (`MESSAGE_NOT_FOUND` if not) and
   classifies it. `message_refs` stays unchanged; no topic column is added.
2. **Topic classification**, from Telegram's `messageReplyHeader` (whose
   `forum_topic` flag is set "except for the General topic"):

   | Anchor | Topic |
   |---|---|
   | peer is not a forum | none, ordinary chat |
   | `reply_to.forum_topic` and `reply_to_top_id` set | `reply_to_top_id` (a reply inside a topic) |
   | `reply_to.forum_topic` and no `reply_to_top_id` | `reply_to_msg_id` (a post directly in the topic) |
   | the anchor is a topic-creation service message | its own ID (the topic root) |
   | peer is a forum and none of the above | the General topic, `id=1` |

3. **Window, ordinary chat.** `messages.GetHistory` with `offset_id=anchor_id`,
   `add_offset=-(after+1)`, `limit=before+after+1`.
4. **Window, named topic.** `messages.GetReplies` with `msg_id=topic_id` and the
   same offset/add_offset/limit. It never crosses into another topic. When the
   anchor is the root, the root came back from step 1 and `getReplies` supplies
   the messages after it; nothing can precede a root inside its topic.
5. **Window, General topic.** General has no reply thread to page, so
   `getReplies` does not apply. Call `messages.GetHistory` around the anchor,
   **drop every message with `forum_topic` set**, and page further out, both
   directions, within the RPC budget until `before`/`after` are filled or the
   budget ends. Fewer neighbours than requested is a legitimate answer, flagged
   with `meta.partial=true`. A message from a named topic is never returned as
   General context.
6. **Trim.** Assert the anchor is present, then trim deterministically to
   `before` older and `after` newer messages, never more than 101 in total.

Ordinary chats and named topics take at most two RPCs; General takes at most
the §13.2 budget. **To verify when the 4c plan is written:** Telegram's
pagination page gives the example `offset_id=MSGID, add_offset=-10, limit=20` and
states no strict `limit > -add_offset` rule. Whether `before=0` (where `limit`
equals `-add_offset`) is accepted gets checked on Test DC. If it isn't, fetch
`before+after+2` and let step 6 trim. The 101 cap is enforced after the trim,
before egress.

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
  1s`, and returned records are post-filtered to `since ≤ sent_at < until`.
  Over-fetching is harmless; under-fetching is the bug.
- **Zero means unbounded.** Telegram applies `min_date`/`max_date` only "if a
  positive value was transferred". So an absent bound is sent as 0 on purpose,
  and a present bound is clamped to at least 1. A `since` at or before the Unix
  epoch can never turn into an accidental 0 that widens the search.
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
  state cap. The cursor stores `window_start` and `next_unstarted_index`, both
  integer indexes into the full universe, and a `per_peer` map keyed by
  `peer_ref` whose entries hold **only** `{"offset_id": int}`.
- **Exhaustion is encoded by absence, not a flag.** A peer in `[window_start,
  next_unstarted_index)` that is missing from `per_peer` is exhausted, and a
  peer at or above `next_unstarted_index` has not been started. This fits the
  validator as it is (no boolean, no new nested key) and cannot be spoofed by
  a stored `false`.
- **Window invariant.** Every peer below `window_start` is exhausted. The
  window advances only past exhausted peers, so a continuation never skips a
  peer. Because the universe is canonical and digest-bound, an index always
  names the same peer.
- **Validator changes, named.** `universe_digest` is a new value class, an
  `hmac-sha256:<64 hex>` string, checked by pattern. `window_start`,
  `next_unstarted_index` and `upper_date` fit the existing integer and `*_date`
  rules. The test from §3.4 (exactly one value rule per allowed key) covers
  all of them.
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
- **Peer cap, cross-project only** (D9). `max_cross_project_peers=250` bounds
  `cross_project_search`. Ordinary `search_messages` has no universe cap; it is
  bounded per call by §13.2 and continues through the window model. When a
  cross-project universe exceeds 250, the gateway searches only its first 250
  canonical peers. `peer_budget` appears in `partial_reasons` on **every** page,
  the last included, and `complete` is never true (CT-139). Continuation never
  goes past the cap. `eligible_peers` reports the true universe size, so the
  gap is visible.
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

### 4.7 Revision 5 refinements for 4c (decided 2026-09-23, from executing the plan)

Every item below came from running the 4c code, not from reading it.

- **The `get_context` window is two requests, one per side (refines §4.1 steps 3–4).** Telegram caps `GetHistory` and `GetReplies` at `limit=100`, and `before+after+1` can reach 101. The edge semantics of `add_offset` are also undocumented. So:
  - The older side reads `offset_id=anchor`, `max_id=anchor`, `limit=before`.
  - The newer side reads `offset_id=anchor`, `add_offset=-(after+1)`, `limit=after+1`, `min_id=anchor`.
  - Each side is sorted nearest-first and trimmed.

  The result is exact whether or not Telegram's window includes `offset_id`. A test serves both semantics and gets identical output, and the first version of the walk failed that test, so the refinement is load-bearing. Ordinary chats and named topics take at most two window requests plus the anchor. `before=0` or `after=0` skips its side. The page cap keeps the anchor first, then the nearest neighbours alternately, so a cut never drops the anchor. The Test DC run still checks the edges on the real server.
- **Owner chat-kind switches are enforced at retrieval for search.** They need dialog data that step 2 cannot fetch. Before a peer is first searched, one batched `GetPeerDialogs` checks it. An excluded peer is never searched. A peer the entity cache cannot reach is never searched either, and it makes the result `telegram_partial`, never `complete`.
- **A search page is held under the response cap by counting bytes, not by dropping records.** Each hit's real encoded size is checked before it is taken. On the first hit that would not fit, that peer resumes *at* the hit (`offset_id = id + 1`), and the page ends with `response_limit`. Nothing examined is lost or repeated. A single hit always fits, because the bounds guarantee it.
- **The hit budget bounds each request.** A per-peer request asks for at most the hits the page, Telegram's 100 ceiling, and the remaining examined-hit budget allow. So `hits_examined` never exceeds 500 (§13.2).
- **The adapter never attributes a response to the wrong peer.** Messages whose `peer_id` is not the requested chat are dropped, and `GetPeerDialogs` returns only the peers that were requested and resolved. Otherwise a response could re-admit an unreachable peer, or put one chat's message into another's results.
- **The coordinator refuses to sign dishonest coverage.** For both searches, the coverage object must exist and match its own §23D chain, the cursor and `meta.partial`, and `hits_returned` must equal the records disclosed. Otherwise the result is `PROOF_GENERATION_FAILED` and nothing is signed.
- **The cursor state has one value rule per key, as a table.** A `per_peer` entry holds only `offset_id`.
- **Smaller decisions:**
  - `has_context` is `true` for every search hit: its ref resolves through `get_context` in any origin project.
  - `search_scope` is `"peer"` when `peer_ref` narrows the search.
  - The cross-project prompt's risk line begins "results may enter this AI session's context".

## 5. Continuation state-machine tests

The Appendix-L formal model covers the coordinator protocol, and continuation
does not alter that protocol, so the model gains no state. Pagination is tested
as its own state machine. Hypothesis is not a dependency, and adding one needs a
§7.2 review, so this uses the same method as `formal/`: exhaustive enumeration
of every interleaving of page, mutate and expire operations up to a fixed depth,
over small `FakeTelegram` universes, plus fixed-seed random runs over large
ones. It covers:

- cursor replay within TTL, and expiry;
- policy mutation between pages (`CURSOR_POLICY_CHANGED`);
- project, grant or egress mutation between pages (`CURSOR_PROJECT_CHANGED`);
- the window transition at peer 64 → 65;
- a cross-project universe of 251: peer 251 is never searched, `peer_budget`
  is on every page, and `complete` is never true;
- an ordinary-search universe of 300: every peer is eventually searched, and a
  complete result is reachable;
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
covers what Telethon actually sends. The allowlist is scoped by operation
(`admin.login`, `admin.discover`, `mcp.retrieval`, §3.3), so a request that is
legitimate during login, such as `GetDifference`, fails the run if it appears
during retrieval. A recorded class outside its phase's list fails the run.

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
- **Secret custody in development is weaker than §9.1.** `api_hash` sits in the
  owner's login Keychain, not a daemon-only store (D2, G14). No Phase-4
  evidence claims §9.1 compliance; that waits for the service-account
  installation.
- **Login pulls content into memory.** Telethon's forced `GetDifference`
  (§3.3) means login can briefly hold recent message content in daemon memory.
  It is never persisted, and the post-login leak sweep checks that. This is a
  memory-exposure bound, not a no-content-read claim.

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
