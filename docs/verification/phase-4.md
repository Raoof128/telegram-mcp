# Phase 4 verification — allowlisted adapter and vertical tool slices

**Status:** Phase 4a complete. `telegram_list_projects` and
`telegram_resolve_project` succeed for real: authenticated loopback ingress,
a daemon-delivered consent prompt answered by the packaged agent, the Phase-3
coordinator, a signed receipt that verifies persisted and offline, ledger
rows, one audit event, and a refreshed anchor. 4b and 4c are not started.

**NOT production.** Nothing here has touched Telegram. No Telegram login,
session file, service account, LaunchAgent or tunnel ingress is claimed. The
other seven sensitive tools still return `POLICY_UNCONFIGURED`.

## Reproducibility

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest -q                                      # 704 passed, 8 skipped
uv run python scripts/e2e_smoke.py                    # 45 passed, 0 failed
uv run pytest tests/formal -q -s                      # 624 states, 18 assertions
uv run ruff check src tests scripts                   # clean
uv run ruff format --check src tests scripts          # clean
uv run mypy src/telegram_mcp                          # clean, 71 files
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

## 7. Follow-ups for 4b

1. Fix `policy.evaluate`'s empty-`owner_allows`-means-allow-all under `allowlist`, with a test, before any peer-scoped success.
2. The live admin presence path over the prompter (needed by `auth login`).
3. The daemon entry point running the lifecycle with these seams.
4. The account row created by login, which ends seeded identity rows.
5. Owner-run: `tests/integration/test_phase4a_touch_id.py --run-platform-gated`.
