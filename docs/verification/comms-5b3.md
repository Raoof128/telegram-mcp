# Comms 5b-3 verification — owner-direct authority (comms spec v0.2)

Design: `docs/superpowers/specs/2026-09-24-comms-5b3-owner-direct-design.md` rev 1 (owner-approved with
amendments A1–A8 and gate additions G1–G2). Plan: `docs/superpowers/plans/2026-09-24-comms-5b3-owner-direct.md`,
executed inline, TDD, with a fail-fast full gate before every commit. Normative output:
`docs/comms-spec-v0.2.md`.

## Rehearsals (throwaway worktree, before the plan was final)

| Rehearsal | Result |
|---|---|
| R1 — JCS byte identity | Byte-identical across the 9 Phase-2 cases, 7 fatal inputs (same exception type and message) and 6 extras. Found that one importer carried a trailing comment, so the repoint had to be AST-based. |
| R2 — v1 receipts across the v2 rebuild | 3 real signed v1 receipts with ledger and audit children verified before and after; rows, IDs and consent values byte-identical; indexes and triggers preserved; `foreign_key_check` empty; `quick_check` ok; chain verifies. Found that the rebuild needs `PRAGMA foreign_keys=OFF` outside the transaction. |
| R3 — dormant WhatsVault sender | No network client in `whatsvault`; providers are `base` and `fake_meta` only; `apps` not importable from the root; console scripts exactly `comms` and `telegram-mcp`; nothing in `src/comms` imports `whatsvault`. |

## Proof, by amendment

| # | Claim | Evidence |
|---|---|---|
| A1 | Receipts are explicitly versioned; verification dispatches on `proof_version`, never on nulls | `proof_version` column + table CHECK (migration 2); `tests/unit/test_receipts_v2.py` (v2 verifies, flipped flag fails, version/shape mismatch fails before any signature work, the table refuses a masquerade) |
| A2 | v1 semantics are never rewritten | `tests/unit/test_receipt_migration.py`: real signed v1 receipts verify before (with the pre-5b-3 verifier) and after; `V1_DIGEST` pins the v1 payload bytes (`test_v1_payload_bytes_are_unchanged`) |
| A3 | Retired keys are inactive, not erased | `tests/unit/test_retired_keys.py`; `doctor` reports them under `retired` (`test_the_key_inventory_reports_retired_keys_as_retired`) |
| A4 | JCS moved as one copy, byte-identical | `src/comms/transports/telegram/canonical.py`; `tests/unit/test_canonical.py` compares against the encoder at the pinned BASE commit on every corpus vector |
| A5 | `soft_threshold_exceeded` is the pre-reservation consult's tier | `tests/integration/test_owner_direct_disclosure.py::test_soft_flag_equals_the_pre_reservation_consult` (receipt and audit event); normal → `0` in `test_a_disclosure_completes_owner_direct` |
| A6 | Replacement formal invariants, each able to fail | `formal/model.py`: 544 states, 22 assertions; `tests/formal/test_invariant_mutations.py` breaks each new guard and requires its named invariant to catch it. The search found one real gap on its first run (a post-commit reservation reaching a later refused request) and the model now guards it |
| A7 | The AI boundary is stated exactly | `docs/comms-spec-v0.2.md` §The AI boundary; `tests/security/test_ai_boundary.py` (no send primitive in either registry; `.claude/settings.json` always-ask) |
| A8 | The dormant WhatsVault sender is unreachable | `tests/security/test_ai_boundary.py` (no network client in `whatsvault` or `apps/`, providers are protocol + fake, `apps` unimportable, exactly two console scripts, no `whatsvault` import under `src/comms`) |
| G1 | The rebuild preserves indexes, FKs, triggers, counts and IDs | `test_the_rebuild_preserves_rows_ids_indexes_triggers_and_counts`; `test_children_still_enforce_the_foreign_key` |
| G2 | Test accounting: unexpectedly missing = 0 | `comms-5b3-collected-before.txt` (1529) → `comms-5b3-collected-after.txt` (1431). 196 IDs left: 183 removed with the consent subsystem, 13 replaced by a named owner-direct test, 0 unexplained (`comms-5b3-classification.json`; pinned by `tests/security/test_test_accounting.py`, which also requires every replacement to be collected). 98 IDs were added. |
| — | Retired identifiers are tombstoned | `tests/security/test_tombstones.py` (none under `src/`, each listed in the spec); `test_comms_protocol_frozen.py` counts every removed occurrence by name |
| — | Admin authority is peer credentials alone | `tests/unit/test_admin_peer_authority.py`; smoke "admin socket routing" |
| — | The pipeline has no consent step | `test_the_coordinator_has_no_consent_seam`; `tests/security/test_comms_layering.py::test_no_consent_modules_remain`; smoke 4a asserts the released receipt is v2 `owner_direct` |

## Gate (branch head)

| Check | Result |
|---|---|
| `uv sync --locked`, contracts | exit 0; OK (23 files — `meta.json` = frozen E.8 + the named v0.2 overlay) |
| Telegram pytest | 1427 passed, 4 skipped (was 1519/10 on `main`; the six skips that went were host-gated consent tests — Touch ID, the join gate, the agent — and the four left are the daemon/install platform gates and the Test DC gate) |
| smoke | 52/52 (was 60: seven consent-phase checks and the RV-1 check are gone); `doctor --production` fails honestly with 7 unmet checks (was 8) |
| formal | 544 states, 22 assertions (was 624/18) |
| ruff / format / mypy | clean; mypy 91 source files |
| build | sdist and wheel built |
| WhatsVault pytest (its own config) | 539 passed — unchanged |

## Rulings and deviations

- **Contract gap (Task 5).** E.8's `proof_payload` is extracted from the SHA-pinned v0.1.10 spec and admitted only v1, so every v2 release failed output validation. The extractor now applies one named, count-asserted overlay to that object alone; the output schema is v2-only.
- **Plan counts.** Task 1's expected count added to the *collected* number (which includes skips); the correct figure was used and matched.
- **`migrate()` and foreign keys (Task 2).** Existing tests caught the rebuild forcing foreign keys on; `migrate()` now restores the caller's prior setting.
- **Frozen-protocol guard (Task 3).** It fired on the new v2 schema string. It was made precise — baseline minus named tombstones plus named additions — not looser.
- **Inventory discipline (Tasks 4, 5).** Two `reserve()` call sites were missed by a truncated grep, and `coordinator._consent` attribute access by a keyword-only sweep; both were caught by the suite, and every later inventory was untruncated and attribute-aware.
- **Order changes.** `admin_approval`/`admin_summaries` were deleted in Task 8 (no user once unwired); Task 10's doctor cleanup was folded into Task 9 (the doctor imported the deleted modules).
- **Admin surface.** `consent status` / `consent approve` leave the §33 surface (51 → 49) rather than answer "not available in this phase", which would imply they return. `PRESENCE_REQUIRED` and CLI exit code 6 are retired.
- **Launcher.** The consent-agent launchd job and `consent.sock` are gone from start/stop/status/sweep; removing the installed agent from this Mac is the owner's runbook step.
- **`tgu_`.** The consent-challenge ref prefix no longer validates.
- **WhatsVault tool names (Task 11).** The plan assumed prefixed names; the six tools are the keys of `build_tool_handlers` in `apps/mcp/server.py`, read by AST.
- **`KEEP_LITERALS`.** The 5b-1 migration record is not edited; the frozen test subtracts a named set of literals whose files were deleted.
- **A flake retired with its verb.** `test_pair_export_import_and_verify_round_trip` failed about 1 run in 64 when a random base64url key began with `-`; the `pair` verb is deleted, and the one surviving positional value (`spki:sha256:…`) cannot begin with `-`.
- **Own errors caught by the gate.** A conftest truncation removed a non-agent fixture (restored verbatim); a duplicate key in the tombstone Counter would have undercounted (merged before it ran).

## Deferred minors

- An intermittent uvicorn lifespan `CancelledError` traceback prints during smoke shutdown (pre-existing; every check passes).

## Runbook (owner-approved, outside every gate)

Delete the paired Secure Enclave approval key and the pairing pins; uninstall the old signed consent-agent bundle and its LaunchAgent.
