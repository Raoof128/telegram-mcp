# Phase 5 verification — operator controls, retention and recovery

Design: [`docs/superpowers/specs/2026-09-24-telegram-mcp-phase-5-design.md`](../superpowers/specs/2026-09-24-telegram-mcp-phase-5-design.md) (rev 2).
Plan (5a): [`docs/superpowers/plans/2026-09-24-telegram-mcp-phase-5a-operator-surface.md`](../superpowers/plans/2026-09-24-telegram-mcp-phase-5a-operator-surface.md) (rev 3).

**Nothing here is a production claim.** Every gate stays PARTIAL. Nothing in 5a touched Telegram: all Telegram-shaped evidence comes from the fake transport.

## 5a — operator surface and one policy engine

Branch `phase-5a`, cut from `fb4b1b6`. Execution order: 1, 2, 3, 4, 4A, 5, 9, 6, 7, 8, 10, 11.

### 5a.1 Scope, as delivered

- **Handler kinds** (`ipc/handlers/_wrapper.py`):
  - `TxCommand` with `parse → plan → apply`, where the transaction is owned only by the runner.
  - `run_audited_tx`, which takes the coordinator's own `APPEND_GUARD`, refreshes the anchor after commit, and latches degraded on anchor failure.
  - `simulate_tx`: `BEGIN IMMEDIATE → SAVEPOINT → ROLLBACK TO → RELEASE → ROLLBACK`.
  - Lock contention becomes the fixed `BUSY` refusal.
- **One evaluator.** `evaluate` and `evaluate_with_trace` share `_decide`. The §10.4 class/archive rule now lives in `authority/policy.py` only. The four retrieval sites ask the evaluator via `admit_live` with live `PeerFacts`, so no retrieval code decides class on its own (Task 4A).
- **`EffectiveAccess`.** Metadata-effective rows carry refs only. A conditional allow is marked `owner_class = unknown`, and class changes are diffed.
- **`policy explain` / `simulate` / `diff`.** Simulate-equals-commit is a semantic diff (minted refs normalised). It is tested over all 16 simulatable commands, including `project create`, `project add-peer` and `project remove-peer`. `tps_` stages are bound to the admin peer, principal, account, security epoch and base digest, and capped at 64.
- **Newly wired:**
  - lock and audit: `lock`, `unlock`, `lock status`, `audit verify`, `audit checkpoint`, `audit repair-anchor`;
  - inspection: `disclosure show`, `disclosure verify`, `disclosure key`, `exposure status`, `consent status`;
  - project, scope and client: `project rename`, `project members`, `project remove-peer`, `project overlap`, `project instruction`, `project grant-cross-search`, `project revoke-cross-search`, `scope remove`, `client disable`;
  - the `serve` CLI verb, pinned.
- **Touch ID summaries.** Every presence-gated admin prompt shows a summary of at most 160 codepoints of what is being approved. The display wire is unchanged.

### 5a.2 Evidence

| Check | Command | Result |
|---|---|---|
| Full suite | `uv run pytest -q` | see §5a.6 |
| Smoke | `uv run python scripts/e2e_smoke.py` | 60 passed, 0 failed (7 new Phase-5a rows over a real admin Unix socket) |
| Simulate = commit, all commands | `tests/unit/test_policy_handlers.py` | 16/16 parametrised, plus the case-list guard |
| No second policy decision path | `tests/security/test_phase5a_architecture.py` | `.admits`/`.decide` only in `authority/policy.py`; decision attributes only there; no `await` inside any transaction; only `_wrapper.py` owns admin transactions |
| Live class through the evaluator | `tests/integration/test_telegram_reads.py`, `test_search_reads.py` | archived / private / group / channel exclusion refused through `get_messages`, and search skips the excluded peer; a spy proves `admit_live` returned `False` |
| Anchor failure recovery | `tests/unit/test_audit_handlers.py::test_an_anchor_failure_is_recovered_by_repair_then_writes_resume` | commit stands → degraded → audited commands refused → `repair-anchor` → writes resume |
| Checkpoints by recorded key | `test_audit_handlers.py` | `none` / `verified` / `failed` (unpublished key, tampered row) |
| Withheld truth | `tests/unit/test_inspect_handlers.py` | `delivery = withheld_audit_unavailable` while latched and after repair |
| Failed rotation has no side effect | `tests/unit/test_phase5a_handlers.py` | busy DB → live seed byte-identical, no `.next` left |
| Command completeness by name | `tests/integration/test_phase5a_completeness.py` | missing = `{doctor, serve}` (CLI) ∪ `{auth revoke-this-session, project drift, policy export, policy import}` (5b/5c) ∪ `{tunnel rotate-binding, release verify, consent approve}` (deferred, with reasons) |

### 5a.3 Shipped defects fixed in 5a

- `scope mode` existed twice (`projects.py`, `scope.py`) with different WHERE clauses. It now exists once.
- `client rotate` set `enabled = 1`, silently undoing `client disable`. It also wrote the live seed before its transaction, with no fsync.
- `scope allow` / `scope deny` / `project add-peer` wrote `peers` in a transaction separate from the policy write.
- `lock` / `unlock` appended no audit event.
- The chat-class rule was decided at four retrieval sites outside `authority/`.

### 5a.4 Shipped gaps recorded for 5b

- `write_checkpoint` has no production caller, so the §26.5 cadence (500 events / 60 min) is unenforced.
- `purge_expired_cursors` has no caller, so spec line 1310's hourly cursor GC does not run (design G10).
- `UserDeactivated*` is still mapped to `SESSION_REVOKED` (design §3.2).
- `verify_chain` rejects a second epoch and a truncated prefix (design G2; `docs/verification/probes/phase5_chain_epochs_probe.py`).

### 5a.5 Gate contributions — all PARTIAL

| Gate | 5a contribution | Missing |
|---|---|---|
| F (privacy) | snapshots, traces, diffs and staging carry refs only (raw-value walk); admin summaries omit secrets | retention purge and backup exclusion (5b/5c) |
| H (operational safety) | every admin mutation on one transaction runner; busy refusals; fail-closed degraded path; presence set pinned by name | revoke, rotation, recovery (5b); installed host (Phase 6) |
| O (disclosure accountability) | audited lock/unlock; checkpoint verification by recorded key; withheld disclosures stay visible | chain epochs and truncation (5b) |
| P (exposure) | `exposure status` per client/project against the thresholds | budget continuity across `privacy-key` rotation (5b, G3) |
| Q (explainability) | `policy explain` traces from the one evaluator; simulate = commit | formal-model crash/restart states (5b) |
| R (release) | none | Phase 7 |

### 5a.6 Final gate

Recorded at the merge-ready head: `uv sync --locked`, the contract check, `pytest`, smoke, formal, `ruff check`, `ruff format --check`, `mypy` and `uv build` all exit 0. The exact counts are in `AGENT.md` for this date.
