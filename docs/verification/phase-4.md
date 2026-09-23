# Phase 4 verification — allowlisted adapter and vertical tool slices

**Status:** Phase 4a complete. `telegram_list_projects` and
`telegram_resolve_project` succeed for real: authenticated loopback ingress,
a daemon-delivered consent prompt answered by the packaged agent, the Phase-3
coordinator, a signed receipt that verifies persisted and offline, ledger
rows, one audit event, and a refreshed anchor. **Phase 4b is on `main`**
(merged at `cd1434e`; see "Phase 4b" below): a daemon, Touch ID admin approvals,
raw reviewed login, and `list_chats`, `resolve_peer`, `get_messages` and
`get_unread` through the same chain, against a fake Telegram transport. **Phase
4c is on `main`** (merged at `0a11bb9`; see "Phase 4c" below):
`telegram_get_context`, `telegram_search_messages` and
`telegram_cross_project_search`, with signed §23D coverage. All nine sensitive
tools are now served, on the fake transport.

**NOT production.** Nothing here has touched Telegram. The Test DC harness
exists but has not been run (owner credentials). No Telegram login, session
file, service account, LaunchAgent or tunnel ingress is claimed. Telegram's
real paging, forum and search behaviour is owner-pending (the Test DC run).

## Reproducibility

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest -q                                      # 1337 passed, 10 skipped (4c)
uv run python scripts/e2e_smoke.py                    # 53 passed, 0 failed (4c)
uv run pytest tests/formal -q -s                      # 624 states, 18 assertions
uv run ruff check src tests scripts                   # clean
uv run ruff format --check src tests scripts          # clean
uv run mypy src/telegram_mcp                          # clean, 87 files (4c)
uv build                                              # sdist + wheel
uv run pytest tests/integration/test_phase4a_touch_id.py --run-platform-gated -q -s   # owner-run, Touch ID
```

## 1. Scope (4a), as the plan states it

- **Identity rows are seeded in 4a.** `projects.account_id` is `NOT NULL`, and the `accounts` row is created by Telegram login (4b). Client registration (`client rotate`) is Phase 5. So in 4a, `accounts`, `principals`, `mcp_clients` and `policy_state` rows come from test fixtures and the smoke. Projects and grants go through the new admin handlers.
- **Admin presence proofs stay injected.** The router checks a `presence` proof with an injected verifier, as Phase 2 does for `lock`. The live proof path, the operator approving an admin challenge on the agent, lands in 4b with `auth login`, which cannot work without it. With no verifier wired, every gated command fails closed with `PRESENCE_REQUIRED`.
- **No daemon entry point.** 4a delivers the composed services and the ingress app, served over real TCP by uvicorn in the integration test and the smoke. The launchd entry that runs the lifecycle with these seams lands in 4b, which needs a live daemon for login.
- **Records stay contract-shaped dicts.** `TelegramReadService` returns the contract `data` objects. Typed domain dataclasses arrive with the Telethon adapter in 4b.
- **Known Phase-2 gap, not fixed here:** `policy.evaluate` treats an *empty* `owner_allows` as "allow all", even in `allowlist` mode. The catalogue tools name no peer, so 4a never reaches that branch. 4b must fix it before any peer-scoped tool succeeds.

## 2. The acceptance path, link by link

```text
HTTP loopback → transport guards → verify_lease → PrincipalContext
  → dispatch validation → SensitiveDispatcher → coordinator
  → MetadataReadAdapter → committed and anchored DisclosureOutcome → MCP response
```

| Link | Proven by |
|---|---|
| Loopback, bearer before parsing | `tests/unit/test_http_guards.py::test_the_gate_refuses_every_bad_case_with_identical_bytes`; `tests/integration/test_ingress.py::test_every_bad_bearer_gets_identical_bytes_before_parsing` |
| Lease → identity only | `tests/unit/test_identity.py` (both tests) |
| Validation, then dispatcher | `tests/integration/test_ingress.py::test_sensitive_tools_without_a_project_answer_honestly` |
| Coordinator with real seams | `tests/integration/test_coordinator_authority.py`, `tests/integration/test_coordinator_consent.py`, `tests/integration/test_coordinator_phase4.py` |
| Consent over the real wire | smoke `catalogue success through ingress + real agent`: the packaged agent (`selftest-rendezvous`) verifies the daemon-signed challenge over RV-1 and signs the approval |
| Receipt, ledger, event, anchor | `tests/integration/test_phase4a_end_to_end.py::test_list_projects_is_a_real_accounted_disclosure`: counts `(1, 1, 1)`, `verify_persisted_receipt`, offline `verify_proof`, anchor file present |
| Real Touch ID | `tests/integration/test_phase4a_touch_id.py`, platform-gated. **Written, not yet run**: it needs the owner at the machine |

## 3. Negative evidence (design §2.8)

| Case | Test |
|---|---|
| Missing, malformed, expired, wrong-epoch, wrong-runtime and unknown-client bearers: identical 401 bytes, before parsing | `test_ingress.py::test_every_bad_bearer_gets_identical_bytes_before_parsing` |
| Disabled client still holding a valid lease | `test_ingress.py::test_disabled_client_is_refused_inside_lease_lifetime` |
| Non-loopback source | `test_http_guards.py::test_the_gate_refuses_every_bad_case_with_identical_bytes` |
| Duplicate keys and oversize bodies never reach the dispatcher | `test_ingress.py::test_duplicate_keys_are_refused_after_auth`, `::test_an_oversize_body_is_refused_and_charges_nothing` |
| Rate limit | `test_ingress.py::test_the_rate_limit_is_429_with_retry_after` |
| Client A cannot see client B's grants | `test_phase4a_end_to_end.py::test_concurrent_bearers_resolve_to_their_own_principal` |
| Grant revoked while the prompt is open | `test_phase4a_end_to_end.py::test_grant_revoked_during_prompt_refuses` (shown to fail when revalidation is disabled) |
| Consent timeout, and no agent | `::test_consent_timeout_is_denied_and_charges_nothing`, `::test_no_agent_is_unavailable_at_once` |
| Cancellation during consent never reaches retrieval | `::test_cancellation_during_consent_never_reaches_retrieval`; `test_coordinator_consent.py::test_cancellation_mid_prompt_propagates_and_leaves_nothing_pending` |
| Same-client concurrency: one prompt per call, no refusal | `::test_same_client_concurrency_prompts_once_per_call` (shown to fail with the per-client lock removed) |
| A late approval never satisfies the next prompt | `test_prompter.py::test_a_late_answer_is_never_matched_to_the_next_prompt` |
| Demo server has no sensitive route | `test_phase4_architecture.py::test_the_demo_server_cannot_reach_sensitive_dispatch`; smoke `demo server still has no sensitive route` |
| The other seven tools | `::test_the_other_seven_tools_still_refuse_honestly`; smoke `other seven tools still refuse` |

The ingress tests were written after the ingress code (the plan's order). The
authentication path was then mutated to admit failed bearers as a real client:
the bad-bearer test went red, and restoring the code turned it green.

## 4. Guard control runs (§37, design §6.1)

Each from a clean tree, each reverted by its single file:

1. `import telethon` appended to `dispatch.py` → `test_only_the_adapter_imports_telethon` failed.
2. A `telegram.metadata` import appended to `server.py` → `test_only_composition_wires_a_backend` and `test_the_demo_server_cannot_reach_sensitive_dispatch` failed.
3. `def read_history(): ...` appended to `telegram/service.py` → `test_no_prohibited_symbol_appears_in_src` failed.

After the reverts, all 8 guards passed from the repository root and from
`tests/`. The source path is anchored to the test file, and an empty file list
fails the run.

## 5. Gate contributions — all PARTIAL

| Gate | 4a contribution | Missing |
|---|---|---|
| B (MCP compatibility) | the same ten tools and schemas served by an authenticated ingress over real TCP; the modern 2026-07-28 envelope exercised through it | installed Codex/Claude builds (Phase 6) |
| E (scope enforcement) | client-project isolation for the catalogue; disabled clients refused at both HTTP and step 8 | peer scope and owner allowlist (4b; the empty-allowlist gap must be fixed first) |
| H (authority/policy) | live SQLite authority, fresh at steps 2 and 8; every mutating admin command presence-gated | the live admin presence path (4b) |
| O (provable disclosure) | a real receipt for a catalogue disclosure verifies persisted and offline | no Telegram-sourced disclosure exists (4b) |
| P (egress/exposure/lock) | the real exposure snapshot is signed into the challenge; same-client serialisation keeps the exact-digest rule from refusing approved calls | Telegram content egress levels (4b) |
| Q (explainability/formal) | the formal model is unchanged and passing on the new coordinator order | pagination state machine (4c) |

## 6. Recorded deviations and implementation choices

- **Dev secret custody (design G14).** Not exercised in 4a; no Telegram credential exists yet.
- **`canonical_request_hmac` key.** The privacy key under domain `telegram-mcp-request/v1\0`. The spec names no key; keying stops arguments (search text, in 4c) being dictionary-guessable from the challenge.
- **Per-client concurrency 1.** Spec §28 permits lowering `max_concurrent_per_client`. Exact-digest consent (§9.8, §23C.3) with per-client buckets otherwise re-prompts and refuses concurrent same-client calls by construction.

## 8. Final whole-branch review

A separate reviewer ran every Review Focus scenario and found no path that releases data or a receipt without verified consent. It raised two Important findings, both fixed test-first:

| Finding | Test (seen failing first) | Evidence after the fix |
|---|---|---|
| A client that disconnects mid-prompt left its challenge live; a late approval then committed a receipt (spec §9.8 requires invalidation) | `test_phase4a_end_to_end.py::test_a_client_that_disconnects_mid_prompt_invalidates_its_challenge` | reviewer probe: pending 0, counts (0,0,0), previously 1 and (1,1,1) |
| An agent that died between prompts was not noticed, so a restarted agent could not attach until a call failed | `test_prompter.py::test_agent_death_while_idle_detaches_and_a_new_agent_can_attach` | reviewer probe: detached at once; late-tap probe still discards the stale answer |

The prompter's single reader loop also removes the mid-frame desync the reviewer listed as minor. The other minors, and the reviewer's "declined to judge" items, are recorded with rulings in the execution ledger. One is a hard prerequisite for 4b: project and peer display names must be bidi- and line-separator-safe before they reach a consent prompt (§9.8).

## 7. Follow-ups for 4b

1. Fix `policy.evaluate`'s empty-`owner_allows`-means-allow-all under `allowlist`, with a test, before any peer-scoped success.
2. The live admin presence path over the prompter (needed by `auth login`).
3. The daemon entry point running the lifecycle with these seams.
4. The account row created by login, which ends seeded identity rows.
5. Owner-run: `tests/integration/test_phase4a_touch_id.py --run-platform-gated`.
6. Make display names prompt-safe (reject or isolate bidi controls, LRM/RLM, U+061C, U+2028/2029) in `project create` and the agent's renderer before any name reaches a prompt.
7. Add an authority check before the first Telegram RPC (revalidation still runs at step 8).

---

# Phase 4b — Telethon adapter, live admin approvals, login, four tools

Plan: `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4b-adapter-and-tools.md`
(revision 4). Design: revision 3 with the §3.8 revision-4 notes. It was
executed inline, task by task, test first. Each commit was made on a green
full gate. The ledger is in the plan's workspace.

## 4b.1 Scope, as delivered

- **Tools.** `telegram_list_chats`, `telegram_resolve_peer`, `telegram_get_messages` and `telegram_get_unread` run through the 4a ingress, consent and coordinator. The universe is the project's readable members, fetched with `GetPeerDialogs`. Pagination is anchored keyset with constant-size state.
- **The adapter owns the wire.** `_GatewayClient._call` makes one send per call: no retry, sleep, flood cache or hidden migrate RPC. Each request must be on the current operation's allowlist and is charged to its work budget. Login is raw reviewed requests; Telethon's helpers are never called. The side-effect review is in `docs/verification/telegram-rpc-review.md`.
- **Admin approvals.** Gated admin commands are approved with Touch ID on the unchanged consent wire. Secrets bind into the signed request by keyed digest, and each token is bound to its exact request.
- **Pre-consent refusal.** Telegram tools refuse before consent without a usable session (`TelethonSession.readiness()`). Authority is re-checked before the first RPC.
- **Retryability.** It comes only from `results.RETRYABILITY`, which is §27.1 verbatim.
- **Topic titles.** `topic_title` is always `null`, because titles need an unreviewed RPC.

## 4b.2 Evidence (default suite and smoke; fake transport)

| Claim | Test |
|---|---|
| No retry, resend or hidden sleep at login | `test_auth_restart_is_one_request_and_no_retry`, `test_auth_restart_is_raised_once_with_no_hidden_sleep` (a real `TelegramClient` with a fake sender), `test_login_is_raw_reviewed_requests_and_never_resends` |
| Unreviewed requests never reach the sender | `test_an_unreviewed_request_is_refused_before_the_client`, `test_outside_an_operation_or_its_allowlist_nothing_is_sent` |
| Approval bound to the exact request | `test_a_token_is_bound_to_the_exact_request_including_secrets`, `test_a_token_expires`, the swapped-args replay in `test_serve_admin_path_gates_through_the_approver` |
| Authority moved during consent → no RPC | `test_scope_mode_change_during_prompt_refuses` (Review Focus 1) |
| Outside-project sender gets no ref | `test_sender_outside_project_gets_null_ref` |
| Pagination never silently ends | `test_a_large_project_pages_to_the_end_with_constant_state` (1,100 chats), `test_a_deleted_message_does_not_end_paging` |
| Reservation dominates the Phase-3 measurement | `test_actual_charge_never_exceeds_the_reservation`, over every tool and egress mode |
| Local logout forgets the in-memory key | `test_logout_local_forgets_the_in_memory_client` |
| Unreachable Telegram is a state, not a crash | `test_an_unreachable_telegram_does_not_stop_the_daemon` |
| No body at rest; only reviewed RPCs | `test_list_chats_then_get_messages_with_receipts_and_no_body_at_rest`; the four smoke rows under "Phase 4b — Telegram reads" |
| Production-shape admin socket | `test_production_shape_socket_is_group_0660_and_needs_the_installer` |

## 4b.3 Control runs (each observed failing, then restored)

- **RPC guard.** A planted `functions.messages.ReadHistoryRequest` in the adapter made `test_every_rpc_reference_is_reviewed` and `test_no_prohibited_symbol_appears_in_src` fail.
- **Estimator.** Weakening `bounds._W` from U+0001 to `a` made all four exposure-invariant cases fail.

## 4b.4 Owner-pending (not run here; no claim made)

1. **Test DC run.** First create the Keychain item: `security add-generic-password -s telegram-mcp -a api_hash -w`. Then run `TG_TESTDC_API_ID=… TG_TESTDC_DC=… TG_TESTDC_IP=… uv run pytest tests/telegram/test_testdc.py --run-telegram-testdc -q -s`. This is the read-state witness:
   - B-side DM and group read markers and the channel post views (independent);
   - A's unread counts (read through the gateway, so not independent);
   - the runtime recorder;
   - the marker leak sweep.

   The fixture builder and witness have never run.
2. **Installed host boundary.** `tests/integration/test_daemon.py::test_the_installed_runtime_directory_has_the_production_boundary --run-platform-gated` skips until the installer has run.
3. **Carried from 4a.** `tests/integration/test_phase4a_touch_id.py --run-platform-gated`.
4. **Signed agent bundle.** `build/consent-signed` does not yet contain the Task 2 renderer fix. Re-package it before relying on the paired agent.

## 4b.5 Recorded deviations

- **Login-Keychain `api_hash`** (design §3.5): dev only, Test DC and the dedicated account only. It closes when the `telegram-mcpd` store exists.
- **Development daemon** (`admin_group` unset): uid-only socket, directory created at `0700`. It is **not** evidence of §9.4 conformity; the production shape is exercised only by the group-mode test and the platform-gated host check.
- **Chat universe** (design §3.8): `GetPeerDialogs` over project members instead of paging `GetDialogs`. This is narrower than §17.4 requires.

## 4b.6 Gate contributions — all still PARTIAL

| Gate | 4b contribution | Missing |
|---|---|---|
| E (scope) | owner mode (an empty allowlist denies), per-member authority, owner chat-kind switches | the live Test DC run |
| H (authority) | a pre-retrieval re-check; live Touch ID admin approvals bound to exact requests | the installed-host run |
| O (provable disclosure) | receipts for Telegram-shaped disclosures (fake transport) | a real Telegram disclosure |
| P (egress/exposure) | egress levels on message text; a reservation proven to dominate the measurement | — |
| R (read-only) | the owned wire boundary, the reviewed set, AST and runtime guards | the Test DC witness and recorder run |

# Phase 4c — context, both searches, coverage and qualification

Plan: `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4c-context-and-search.md`
(revision 6: execution-first, a line-by-line gauntlet, and an external
review). Design: revision 5/6, §4.7. It was executed inline, task by task and
test first: every red step was watched failing, and every commit was made on a
green full gate. The ledger is in the plan's workspace.

## 4c.1 Scope, as delivered

- **Tools.** `telegram_get_context`, `telegram_search_messages` and `telegram_cross_project_search` run through the 4a ingress, consent and coordinator.
- **`get_context`.** A historical `message_ref` resolves to its canonical peer, which must pass *current* owner policy and membership. The window is two bounded requests, one per side, exact under either `add_offset` edge semantics. A named forum topic reads through `GetReplies` only, and General drops named-topic messages. The page cap never drops the anchor. The forum flag comes from the dialog as well as the by-id response.
- **Searches.** Per peer only, never `messages.searchGlobal`. A pure continuation engine (`telegram/search.py`) walks a canonical, digest-bound universe through a 64-peer window, round-robin. Cross-project search is capped at 250 peers with `peer_budget`. Each record takes the most restrictive grant among its own origin projects.
- **Refinements (design §4.7), each named:**
  - two-request context windows;
  - owner chat-kind switches enforced at retrieval, in one batched dialog request per call;
  - byte- and codepoint-accounted pages (`PageBudget`, one copy of both §13.2 caps, also serving the 4b tools);
  - requests bounded by the hit budget;
  - attribution checks: no foreign-chat entry, no unrequested dialog;
  - the coverage consistency gate at the coordinator;
  - one value rule per cursor key;
  - a page ends on Telegram's own signal, never on a short page (`_page_end`, also correcting 4b's `fetch_history`);
  - sticky uncertainty;
  - measured coverage counters, including owner-excluded peers leaving the eligible set;
  - 10 search pages per call;
  - one RFC 3339 parser, with a fractional `until` rounded up;
  - the epoch edge.

## 4c.2 Evidence (default suite and smoke; fake transport)

| Claim (plan Review Focus) | Test |
|---|---|
| Deleted/edited messages between pages: nothing lost or repeated | `test_exhaustively_every_hit_arrives_exactly_once_and_it_ends` (every universe of up to three peers, three limits, three RPC budgets), `test_a_page_held_under_the_cap_loses_and_repeats_nothing` |
| The context window never leaves a topic or shifts by one | `test_an_ordinary_window_is_exact_under_either_edge_semantics`, `test_a_named_topic_window_uses_replies_and_never_history`, `test_general_never_includes_a_named_topic_message`, `test_forum_classification_comes_from_the_dialog` |
| A shared peer removed mid-flight is discarded, with no receipt | `test_a_shared_peer_removed_mid_flight_is_discarded`, `test_a_shared_peer_removed_between_retrieve_and_commit_is_discarded`, a smoke row |
| Long hits held under both caps | `test_a_long_page_is_held_under_the_cap_without_losing_hits`, `test_a_search_page_holds_combined_text_under_32000_codepoints`, `test_a_page_holds_both_caps_for_every_script` (ASCII, Persian, combining mark, emoji, ZWNJ) |
| Unreachable peer or foreign-chat entry never claimed complete | `test_an_unreachable_member_is_never_claimed_complete`, `test_messages_from_another_chat_are_never_attributed`, `test_a_foreign_message_never_steers_the_offset_and_is_never_complete` |
| Continuation after policy, grant, egress or membership change | `test_the_cursor_hierarchy_then_the_universe_digest` |
| Inconsistent coverage is never signed | `test_inconsistent_coverage_is_never_signed`, `test_search_carries_coverage_bound_into_the_signed_proof` |
| A short page is not the end (Telethon 1.45.0 `messages.py:213-225`) | `test_the_last_page_is_telegrams_own_signal_not_a_short_page`, `test_history_keeps_paging_past_a_short_slice` |
| Uncertainty sticks to the whole continuation | `test_telegram_uncertainty_sticks_to_the_whole_continuation`, `test_telegram_uncertainty_survives_the_cursor` |
| Measured counters; owner scope shapes eligibility; ten pages | `test_telegram_rpcs_and_hits_examined_are_measured_not_guessed`, `test_coverage_counts_every_telegram_request_the_call_made`, `test_an_owner_excluded_peer_leaves_the_eligible_set`, `test_an_owner_excluded_peer_is_not_eligible`, `test_search_pages_are_bounded_at_ten_per_call` (9, 10, 11) |
| Query text never leaves memory | `test_query_text_never_reaches_the_database`, `test_the_query_text_never_leaves_memory` (every file under the runtime directory and every log line, on success and failure) |
| Reservation dominates the Phase-3 measurement | `test_actual_charge_never_exceeds_the_reservation` (4c, every egress mode) |

The smoke's four Phase-4c rows:
- `search_messages` with signed coverage;
- `get_context` through ingress;
- the cross-project race discarded at step 8;
- never a global search.

**Staged proof.** Before the plan was written, each task was replayed onto a fresh `main` export with its own tests, lint, types, the full suite and the smoke; all 48 checks were green. The plan text was then replayed mechanically: 38 blocks, 36 files, byte-identical to the tested tree. During execution, every task's red and green outputs and its full-gate counts matched the plan exactly.

## 4c.3 Control runs (each observed failing, then restored)

- **Estimator.** Weakening `bounds._W` from U+0001 to `a` made all three 4c exposure-invariant cases fail.
- **Query leak.** A planted debug log of the query made the canary test fail in the captured log. A planted `INSERT` of the query made it fail in `meta.db-wal`.

## 4c.4 Owner-pending (not run here; no claim made)

1. **Test DC run.** See §4b.4 for the Keychain item and variables. The harness now also covers:
   - forum topic isolation and General filtering;
   - the `before=0`/`after=0` edges on the real server;
   - real search exhaustion across the project;
   - Telegram's own paging: `search_peer` two hits a page over the forum's nine marker messages.

   The witness does not read the forum's markers; a forum witness is a follow-up.
2. **Dedicated non-primary account** (design §4.6): a re-run of the same harness.
3. **Carried from §4b.4:** the Touch ID test, the installed-host boundary, and re-packaging the signed agent bundle.

## 4c.5 Recorded deviations

- **The universe digest binds the candidate universe** (authority-derived). Owner scope is applied live, as each peer is reached, and re-checked on every call. A peer excluded when reached, then unarchived, stays counted as excluded for that continuation.
- **The ten-page bound reports `rpc_budget`,** because §23D's closed reason list has no page reason.

## 4c.6 Gate contributions — all still PARTIAL

| Gate | 4c contribution | Missing |
|---|---|---|
| E (scope) | cross-project isolation, per-record egress intersection, owner scope in eligibility | the live Test DC run |
| O (provable disclosure) | signed §23D coverage, a consistency gate, measured counters | a real Telegram search |
| P (egress/exposure) | both §13.2 response caps enforced before commit, for every read | — |
| Q (continuation) | the continuation state machine, exhaustive over small universes | the dedicated-account qualification |
| R (read-only) | two more reviewed reads (`messages.Search`, `messages.GetReplies`); still no global search | the Test DC witness and recorder run |
