# Comms 5b-4 verification: the campaign core (fake transports only)

Design: `docs/superpowers/specs/2026-09-24-comms-5b4-campaign-core-design.md` **rev 4** (`b9d5e36`).
Its SHA-256 is `dc61a47c48d5654e90fd94da69dc22a0f4106ea7d83bb4e8c2c6f1500ba91851`, checked before Task 1
and again at evidence time (G10). Plan: `docs/superpowers/plans/2026-09-24-comms-5b4-campaign-core.md` rev 3,
executed inline and test-first, with a fail-fast full gate before every commit. Source requirements:
`docs/provenance/comms-gateway-v0.1.md` §§4–19, §31, §32.

Nothing here talks to Telegram or Meta. Every transport is a fake under `tests/core/fakes.py`.

## What exists now

`comms.core` holds the transport-neutral campaign core:

| Module | Contents |
|---|---|
| `canonical`, `opaque` | Moved from the Telegram tree; single copies. |
| `refs`, `timeutil`, `domains` | Supporting primitives. |
| `storage` | SQLCipher `comms.db` and atomic migrations. |
| `campaigns` | Directory, resolution with origin paths, drafts and the typed event log. |
| `delivery` | Transport contract, reducer, freeze, engine, operations, scheduling and recovery. |

The formal model is `formal/campaign_model.py`.

## Measured facts (M1–M11)

| # | Fact | Where it is pinned |
|---|---|---|
| M1 | SQLCipher 4.12.0 accepts a wrong key and fails only at the first read. A keyless open of a new file creates a **plaintext** database. An empty key raises. | `test_comms_db.py`: wrong key fails at open; key refused before any file exists; header asserted non-plaintext |
| M2 | Opening costs 28 ms. Inserting 5,000 jobs and 5,000 origins costs 88 ms. | `test_freeze_budget.py`: the whole 5,000-endpoint freeze measured at **341 ms** against a 3 s budget |
| M3 | The core's import, string and dynamic-import guards. | `test_comms_layering.py::test_core_is_transport_neutral` |
| M4 | Core prefixes are disjoint from Telegram's (including `tgu_`) and WhatsVault's (`aud`, `job`, …). | `test_refs_and_time.py::test_core_prefixes_are_disjoint_from_telegram_and_whatsvault` |
| M5 | 31 importers repointed. `canonical`/`opaque` are stdlib-only. | `test_core_moves.py`; the BASE oracle byte-compares every JCS vector |
| M6 | Circular FKs work. Expression and partial unique indexes enforce. sqlcipher3 raises its own `IntegrityError`. | `test_schema.py` (55 tests) |
| M7 | Lock contention times out after 5 s. Connections are thread-bound. | `test_lock_contention_raises_after_the_timeout`; `test_claim_is_compare_and_set` interleaves two connections in one thread |
| M8 | mypy needs a `sqlcipher3.*` override. `comms-*` domains were unguarded. | `pyproject.toml`; `tests/security/test_comms_wire_frozen.py` |
| M9 | A `TEMP` trigger on a main table aborts mid-transaction and rolls back, with no seam in `src`. | `fakes.plant_failure`; `test_planted_failure_aborts_and_rolls_back` |
| M10 | JSON1 is available (SQLite 3.51.1). | the `job_origins` `CHECK(json_extract(path,'$[#-1]') = endpoint_ref)` |
| M11 | A failed migration rolls back its DDL and its version row. | `test_failed_migration_does_not_advance_version`, `test_failed_migration_leaves_no_partial_schema` |

`cipher_version` = `4.12.0 community`.

## Design findings to tests

| # | Test(s) |
|---|---|
| R1, R2, H5 | `test_comms_db.py` (wrong key, short/missing key before any file, encrypted header, keyless sqlite read fails, key never in errors) |
| R3, S1, S2 | `test_directory.py::test_destination_and_contact_point_share_one_identity`, `test_peer_kinds_stay_distinct`; `test_freeze.py::test_same_identity_via_destination_and_contact_point_gets_one_job`; `test_resolve.py::test_a_group_sharing_the_raw_number_is_a_different_candidate` |
| R4 | `test_reducer.py` (exhaustive table, late webhook, earlier-attempt failure); `test_operations.py::test_accepted_then_provider_failure_moves_summary_sent_to_partial_without_touching_lifecycle` |
| R5 | `test_engine.py::test_a_raised_deliver_is_outcome_unknown_never_failed`; `ResultKind` docstring (`test_result_kind_contract_is_documented`) |
| R6 | `test_freeze.py::test_edit_and_reschedule_mints_new_keys`, `test_idempotency_key_binds_generation` |
| R7 | `test_scheduling_and_recovery.py::test_the_stranded_sending_state_is_completed`; model property `NeverStrandedInSending` |
| R8 | `test_freeze.py::test_zero_eligible_endpoints_refuses_and_leaves_ready`; `test_reducer.py::test_summarize_refuses_empty`; model `NoEmptyGeneration` |
| R9, H1 | `test_engine.py::test_revalidation_suppresses_a_member_removed_after_freeze`, `test_revalidation_never_adds`; model `ExecutionSetWithinFrozenSet` |
| R10, H2 | `test_transport_contract.py` (frozen `FrozenDelivery`, fakes) |
| R11 | `test_schema.py::test_immutability_triggers`; `test_directory.py::test_disable_old_create_new_with_the_same_identity_is_allowed` |
| R12 | `test_privacy.py::test_canaries_absent_from_every_file_in_the_database_directory` (delete and WAL) |
| R13 | `test_operations.py::test_same_event_ref_on_two_transports_is_two_events`, `test_duplicate_webhook_is_harmless` |
| R14 | `test_freeze.py::test_snapshot_digest_is_stable` (in-process and subprocess), `test_snapshot_digest_moves_with_every_field`, `test_the_freeze_commits_the_digest_of_what_it_wrote` |
| R15 | `test_drafts_and_events.py::test_a_state_change_is_rolled_back_with_its_event`, `test_event_seq_is_global_and_increasing` |
| R16 | `test_refs_and_time.py`; `test_scheduling_and_recovery.py::test_offset_schedule_time_compares_as_utc` |
| R17 | `tests/formal/test_campaign_model.py`, `test_campaign_model_mutations.py` |
| R18, C1, S3 | `test_engine.py::test_deliver_is_never_called_inside_a_transaction`; `test_freeze.py::test_no_deliver_happens_during_freeze`; the `DeliveryTransport` docstring |
| R19 | `test_engine.py::test_window_closed_between_schedule_and_send_skips` |
| R20 | `test_engine.py::test_claim_is_compare_and_set`, `test_execute_requires_the_lease`; `test_recover_and_run_due_require_the_lease` |
| R21 | `tests/security/test_ai_boundary.py::test_no_ai_surface_imports_the_campaign_core` (+ planted import) |
| R22 | `test_retry_exhausted_at_the_cap`; `test_freeze.py::test_digest_builders_accept_only_refs`; `test_freeze_budget.py` |
| S4 | `test_operations.py::test_webhook_before_result_is_reconciled_when_the_result_binds`; model `EarlyProviderUpdateNeverLost` |
| S5 | `test_freeze.py::test_three_digests_are_distinct_and_domain_separated` |
| S6 | `test_drafts_and_events.py::test_event_payload_refuses_free_text_under_every_key` |
| S7 | `test_engine.py::test_a_core_error_propagates_and_is_never_an_outcome` |
| S8 | `test_freeze.py::test_unschedule_with_a_skipped_job_cancels_only_pending` |
| S9 | `test_scheduling_and_recovery.py::test_recover_never_calls_a_transport_and_reports_resumable` |
| S10 | `test_schema.py::test_an_origin_must_resolve_to_its_jobs_identity`, `test_an_origin_path_must_end_at_its_endpoint` |
| S11 | `test_operations.py::test_duplicate_webhook_with_different_contents_is_a_recorded_conflict` |
| S12 | `test_operations.py::test_resolve_not_sent_then_retry_makes_a_new_attempt`; model `UnknownGainsAttemptOnlyAfterResolveNotSent` |
| C2 | `test_reducer.py::test_unknown_is_never_failed`; model `UnknownNeverYieldsFailed` |
| C3 | `test_operations.py` (cancel and retry act on jobs) |
| C4 | `test_operations.py::test_retry_reuses_the_key_and_adds_an_attempt` |
| C5 | `test_resolve.py` (recipient versus contact point sendability) |
| C6 | `test_scheduling_and_recovery.py::test_restart_before_send_at_sends_nothing`; model `NoExecutionBeforeSendAt` |
| H3 | `test_operations.py` provider-update tests |
| H4 | `events.py` docstring; `test_event_types_are_exactly_the_spec_list` |
| H6 | `test_core_moves.py`; `test_refs_and_time.py` |
| H7 | `test_freeze.py::test_unschedule_and_scheduled_cancel_clear_the_current_generation` |

The source §31 list maps line by line in `tests/core/test_source_checklist.py`. Its `test_every_section_31_line_is_mapped_to_an_existing_test` parses §31 from the provenance file, requires all 22 lines to be mapped, and requires every mapped test to exist.

## The crash table (§10), one test per row

`test_scheduling_and_recovery.py` has one test per row:

| Crash point | Test |
|---|---|
| Before the freeze commits | `test_crash_before_the_freeze_commits` |
| After freeze, before claim | `test_crash_after_freeze_before_claim` |
| After claim, or during deliver | `test_crash_after_claim_or_during_deliver_is_outcome_unknown` |
| Provider accepted, before the outcome commits | `test_provider_accepted_before_the_outcome_commits_is_never_resent` (exactly one transmission ever) |
| After the last outcome commits | `test_crash_after_the_last_outcome_commits` |
| Stranded `SENDING` | `test_the_stranded_sending_state_is_completed` |
| Unschedule, cancel, retry or resolution | `test_operator_operations_are_all_or_nothing` |

The engine's own seams are covered by `test_engine.py::test_crash_seams_leave_the_section_10_state`.

## Bounded model, mutations, differential walk

- **Model.** `formal/campaign_model.py` imports nothing from `src`. It explores **96,528 reachable states** and **11 properties** in 8.4 s, with 2 jobs, 2 generations and a retry cap of 2. No violations.
- **Mutations.** 11 mutations, one per property, each caught by its own property. `explore(stop_on=…)` returns at the first violation, so each mutant takes 0.03–0.18 s.
- **Differential walk.** 300 seeded sequences, **3,803 steps**, 3.1 s. The model and the library agree on job states, lifecycle and summary after every step. `test_the_walk_catches_a_planted_library_defect` plants a `summarize` bug and requires a divergence.

## Gate (branch head)

| Check | Result |
|---|---|
| `uv sync --locked`, contracts | exit 0 |
| Telegram pytest | 2214 passed, 4 skipped (was 1427/4 at `main`) |
| smoke | 52/52 |
| formal | 544 states / 22 assertions (unchanged); campaign model 96,528 states / 11 properties; 25 formal tests |
| ruff / format / mypy | clean; mypy 110 source files |
| build | sdist and wheel built |
| WhatsVault pytest (its own config) | 539 passed; the subtree is byte-identical to `main` |

## Rulings (from the execution ledger)

1. **Pre-flight.** `freeze.cancel` on a `SENDING` campaign delegates to `operations.cancel_sending`.
2. **Task 2.** `migrate()` refuses a gapped or unordered migration tuple.
3. **Task 3.** Destinations and contact points are never deleted (a trigger), because origins now name them by ref rather than by FK.
4. **Task 4.** Enabling an endpoint re-checks the one-enabled rules with fixed messages. A normalizer's exception becomes `DirectoryError("invalid platform identity")` from `None`.
5. **Task 5.** A location resolves to its destinations and member recipients (source §4).
6. **Task 7.** `check_targets` is the one copy of target checking.
7. **Task 8, completion.** `reduce()` completes an idle campaign in the transaction of the last job change, for every caller.
8. **Task 8, decide table.** The table's refinements are pinned by the exhaustive test: `RESULT` after the provider's own report is `recorded`, and provider `FAILED_PERMANENT` may resolve an unknown.
9. **Task 9, snapshot digest.** The snapshot digest is **not** placed in events. The spec contradicts itself here (see below).
10. **Task 9, events and digests.** `campaign.transport_completed` is emitted only while the campaign is `SENDING`. Freeze verifies each payload digest against its data.
11. **Task 10.** The `during_deliver` seam fires before transmission. `still_valid` must return exactly `True` to keep a job.
12. **Task 11.** Bind-time ambiguity emits `AMBIGUOUS_MATCH`. `retry_failed` moves `COMPLETE → SENDING` before requeueing.
13. **Task 12.** A scheduled campaign's start appends `campaign.send_started`.
14. **Task 14, model bound.** The model bound is **2 jobs, not 3**. With three jobs, exploration did not finish in 90 s, over the 60 s ceiling (P12).
15. **Task 14, walk scope.** The walk applies freeze and invalidate only when the model enables them for bound reasons, and provider updates only for bound references.

## Open items for the owner

- **Spec contradiction (rev 4, §5.4 vs §9/R22).** The snapshot preimage includes `delivery_identity` and payload digests. §9 and R22 require every digest in events to be taken over opaque refs only. A one-job snapshot with known content can be brute-forced back to a phone number. The implementation therefore keeps the snapshot digest in the encrypted `generations` row only, and events carry `target_digest` and `recipient_digest`. Either confirm this, or specify a keyed snapshot commitment for 5c's chain.
- **The model covers 2 jobs.** The ceiling forced it. Three-identity behaviour is covered by the unit tests and the walk's two-identity sequences, not by exhaustive search.
- **Out of scope, as designed.** The CLI and admin commands (5e), real adapters and webhooks with §7.2 conformance tests (5d), and the chain-backed audit (5c).
