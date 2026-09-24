# Telegram MCP Phase 5a — Operator Surface and One Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision:** 3 (owner's review folded in; see the Revision 3 table at the end).

**Goal:** Wire the Phase-5a operator commands through the daemon's admin socket. They run on three handler kinds and one policy evaluator that records its steps (a trace), and `policy explain` / `simulate` / `diff` work as a rolled-back dry run over metadata.

**Architecture:** Every admin mutation becomes `parse → plan → apply`. Only `ipc/handlers/_wrapper.py` owns the transaction. Commands that append to the audit chain go through the same append guard and anchor refresh as disclosures. `authority/policy.py` gains `evaluate_with_trace`, which shares one decision function with `evaluate`. It also becomes the only home of the owner class/archive rule. `EffectiveAccess` evaluates every client × project × member row from stored metadata, and simulation runs the real `plan`/`apply` inside `BEGIN IMMEDIATE → SAVEPOINT → ROLLBACK`.

**Tech Stack:** Python 3.12, stdlib `sqlite3`, `cryptography==50.0.1`, pytest, ruff, mypy. No new dependency.

**Spec:** [`docs/superpowers/specs/2026-09-24-telegram-mcp-phase-5-design.md`](../specs/2026-09-24-telegram-mcp-phase-5-design.md), revision 2 (§1, §2, §0B G4–G6, G12–G14, G16). The frozen product spec is `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`).

**Branch:** `phase-5a`, cut from `main` at the commit that carries this plan.

**Execution order:** 1, 2, 3, 4, 4A, 5, 9, 6, 7, 8, 10, 11. Task 6's all-commands test needs Task 9's `member_commands` and `client disable`.

## Global Constraints

- The ten MCP tool contracts, TG-JCS-v1, the challenge wire, RV-1, the prompt frames and `tgml1` do not change.
- **One copy of each shared rule:** the strict JSON decoder, the JCS encoder (`consent.challenge.jcs_dumps`), the frame codec, the opaque-ref minter (`opaque.mint_opaque_ref`), the append guard, and the owner class/archive rule.
- **Fail closed.** A check that cannot be performed reports that it was skipped, never that it succeeded.
- **No test-only flags in production paths.** Tests inject seams.
- Errors carry fixed, non-enumerating strings. Admin validation raises `ValueError`; a refused operation raises `PermissionError`. The router maps them to `MALFORMED_REQUEST` / `PERMISSION_DENIED`.
- `PRESENCE_GATED` stays exactly the frozen 31-name set in 5a. (Phase-5 verbs that add to it land in 5b/5c.)
- No `await` inside a transaction. No transaction is opened, committed or rolled back in `ipc/handlers/` except in `_wrapper.py`.
- Nothing prints or stores message bodies, search text, phone numbers or raw Telegram IDs. Snapshots and diffs carry opaque refs only.
- Uniform `ValueError` for admin validation carries `# noqa: TRY004 -- uniform ValueError on admin validation` where ruff asks.
- Socket tests `chdir` into `tmp_path` and use relative paths (AF_UNIX's ~104-byte limit).
- Before claiming the plan is done, the full gate passes:
  `uv sync --locked`, `uv run python scripts/extract_contracts.py --check`, `uv run pytest -q`, `uv run python scripts/e2e_smoke.py`, `uv run pytest tests/formal -q -s`, `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src/telegram_mcp`, `uv build`.
- Commit only on a green gate. Never pipe a gate command through anything that masks its exit status.
- Append a dated `**Raouf:**` entry to `AGENT.md` and `CHANGELOG.md` at the end (Task 11).

## Review Focus

1. **A simulation or admin write is attempted while another connection holds the write lock.** Measured on this tree: `BEGIN IMMEDIATE` waits for `open_db`'s ~5 s busy timeout and then raises `sqlite3.OperationalError: database is locked`, which the router would report as `INTERNAL_ERROR`. It must instead fail with the fixed `ValueError("the database is busy; retry")`, and it must never leave the connection inside an open transaction. *Test: Task 2, `test_a_busy_database_is_a_fixed_refusal`.*
2. **A stale `tgl_` handle is used after the epoch moved, whether or not the post-commit eviction ran.** It must be refused. *Test: Task 3, `test_a_failed_eviction_still_refuses_stale_handles`; Task 9, `test_member_handles_die_with_the_project_epoch`. The converse, that a dry run spends no handle, is Task 6's `test_simulation_leaves_a_selection_handle_usable`.*
3. **An audited admin command runs while the audit chain is degraded.** It must be refused before any row changes. The one exception is `audit repair-anchor`. *Test: Task 7, `test_lock_is_refused_while_degraded_and_nothing_changes`.*
4. **`policy explain` or `simulate` output could carry a raw Telegram ID.** No value in any snapshot row, trace or diff may contain a `type:id` identity or a bare Telegram ID. *Test: Task 5, `test_no_row_carries_a_raw_identity`.*
5. **An operator runs `policy simulate` on something that is not a policy mutation** (`lock`, `auth login`, `policy simulate` itself). It must be refused with a fixed error and no state change. *Test: Task 6, `test_simulate_refuses_non_policy_commands`.*

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `src/telegram_mcp/disclosure/audit/chain.py` (modify) | 1 | `APPEND_GUARD` and `EVENT_COLUMNS` made public; `insert_checkpoint` (no commit). |
| `src/telegram_mcp/disclosure/audit/anchor.py` (modify) | 1 | `latch_degraded`, the single copy. |
| `src/telegram_mcp/disclosure/coordinator.py` (modify) | 1 | Uses the shared guard and latch. |
| `src/telegram_mcp/storage/refstore.py` (modify) | 1 | `ensure_peer_in_tx`. |
| `src/telegram_mcp/storage/db.py` (modify) | 1 | `write_epoch_state` (in-TX body of `save_epoch_state`). |
| `src/telegram_mcp/ipc/handlers/_wrapper.py` (create) | 2 | `TxCommand`, `run_tx`, `simulate_tx`, `AuditSink`, `admin_event`, `run_audited_tx`, `tx_handler`. |
| `src/telegram_mcp/ipc/handlers/projects.py` (rewrite) | 3, 9 | Pure policy mutations as `TxCommand`s, including the single `scope mode`. |
| `src/telegram_mcp/ipc/handlers/scope.py` (rewrite) | 3, 9 | Discover (flow), allow/deny/remove/add-peer as `TxCommand`s. |
| `src/telegram_mcp/ipc/handlers/clients.py` (rewrite) | 3, 9 | `client rotate` (G11) and `client disable`. |
| `src/telegram_mcp/authority/policy.py` (modify) | 4 | `PeerFacts`, `OwnerScope` (moved here), `evaluate_with_trace`, class step. |
| `src/telegram_mcp/disclosure/seams.py` (modify) | 4 | `OwnerScope` re-exported, not defined. |
| `src/telegram_mcp/storage/authority_view.py` (modify) | 4 | `load_view` loads `owner_scope`. |
| `src/telegram_mcp/disclosure/seams.py`, `search_authority.py`, `telegram/reads.py` (modify) | 4A | Snapshots carry `view`; the four retrieval sites call `admit_live`. |
| `src/telegram_mcp/ipc/admin.py` (modify) | 6 | `ADMIN_PEER` context variable. |
| `src/telegram_mcp/disclosure/keys.py` (modify) | 7 | `ensure_current_published`, the single copy. |
| `src/telegram_mcp/authority/effective.py` (create) | 5 | Pure `AccessRow`, `effective_rows`, `diff_rows`, `rows_digest`. |
| `src/telegram_mcp/storage/effective_access.py` (create) | 5 | `snapshot(conn)`, `explain(...)`, `base_digest(conn)`. |
| `src/telegram_mcp/authority/staging.py` (create) | 6 | `StagingRegistry` (`tps_`). |
| `src/telegram_mcp/ipc/handlers/policy.py` (create) | 6 | `policy explain/simulate/diff`. |
| `src/telegram_mcp/ipc/handlers/audit.py` (create) | 7 | `lock`, `unlock`, `lock status`, `audit verify/checkpoint/repair-anchor`. |
| `src/telegram_mcp/disclosure/lineage.py` (create) | 8 | `LineageVerdict`, `NoRestoreLineage`. |
| `src/telegram_mcp/ipc/handlers/inspect.py` (create) | 8 | `disclosure show/verify/key`, `exposure status`, `consent status`. |
| `src/telegram_mcp/consent/admin_summaries.py` (create) | 10 | `summarize(command, args)`, at most 160 codepoints. |
| `src/telegram_mcp/consent/admin_approval.py` (modify) | 10 | `action_display = summarize(...)`. |
| `src/telegram_mcp/runtime/composition.py` (modify) | 11 | `admin_handlers(...)`, the one assembly point. |
| `src/telegram_mcp/cli.py` (modify) | 11 | The `serve` verb, pinned to `EXIT_NOT_IN_PHASE`. |
| `scripts/e2e_smoke.py` (modify) | 11 | A `phase5a_operator` section. |
| `docs/verification/phase-5.md` (create) | 11 | The 5a evidence ledger. |

Tests are listed in each task.

---

### Task 1: Make the shared audit and storage primitives composable

The design needs these building blocks to run **inside a caller's transaction**. Today four of them commit or lock privately (0B G5, and `RefStore.ensure_peer`, `save_epoch_state`, `write_checkpoint`).

**Files:**
- Modify: `src/telegram_mcp/disclosure/audit/chain.py` (`_EVENT_COLUMNS` at :73, `write_checkpoint` at :281)
- Modify: `src/telegram_mcp/disclosure/audit/anchor.py`
- Modify: `src/telegram_mcp/disclosure/coordinator.py:76,318-321,513`
- Modify: `src/telegram_mcp/storage/refstore.py:51-86`
- Modify: `src/telegram_mcp/storage/db.py:290-326`
- Test: `tests/unit/test_phase5a_primitives.py`

**Interfaces:**
- Produces:
  - `chain.APPEND_GUARD: threading.Lock`
  - `chain.EVENT_COLUMNS: tuple[str, ...]`
  - `chain.insert_checkpoint(conn, checkpoint_key: bytes, *, now: str) -> dict[str, Any]`, which requires an open transaction
  - `anchor.latch_degraded(conn, *, reason: str, disclosure_ref: str = "") -> None`
  - `RefStore.ensure_peer_in_tx(peer_type, peer_id, *, display_name, username) -> PeerRow`
  - `db.write_epoch_state(conn, state) -> None`, which requires an open transaction

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_phase5a_primitives.py
"""Phase-5a primitives compose inside a caller-owned transaction (design §2.1, 0B G5)."""

import secrets

import pytest

from telegram_mcp.authority.epochs import set_locked
from telegram_mcp.disclosure.audit import anchor, chain
from telegram_mcp.disclosure.audit.chain import immediate_transaction
from telegram_mcp.storage.db import bind_epoch_state, open_db, write_epoch_state
from telegram_mcp.storage.refstore import RefStore
from telegram_mcp.storage.settings import get_setting
from tests.authority_fixtures import seed_authority_rows

NOW = "2026-09-24T00:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    return c


def _event(tool="admin.lock"):
    e = {c: None for c in chain.EVENT_COLUMNS}
    e.update(event_id=chain.mint_event_id(), ts=NOW, tool_name=tool, status="ok")
    return e


def test_the_append_guard_is_the_coordinators_guard():
    from telegram_mcp.disclosure import coordinator

    assert coordinator.APPEND_GUARD is chain.APPEND_GUARD


def test_insert_checkpoint_rolls_back_with_its_transaction(conn):
    key = secrets.token_bytes(32)
    with immediate_transaction(conn):
        chain.append_event(conn, key, _event())
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        chain.insert_checkpoint(conn, secrets.token_bytes(32), now=NOW)
        raise RuntimeError("abort")
    assert conn.execute("SELECT COUNT(*) FROM audit_checkpoints").fetchone()[0] == 0


def test_insert_checkpoint_refuses_outside_a_transaction(conn):
    with pytest.raises(chain.ChainError):
        chain.insert_checkpoint(conn, secrets.token_bytes(32), now=NOW)


def test_latch_degraded_sets_all_three_settings(conn):
    anchor.latch_degraded(conn, reason="anchor_refresh_failure")
    assert get_setting(conn, "audit.integrity_degraded") == 1
    assert get_setting(conn, "audit.degraded_reason") == "anchor_refresh_failure"
    assert get_setting(conn, "audit.degraded_disclosure_ref") == ""


def test_ensure_peer_in_tx_rolls_back_with_its_transaction(conn):
    refs = RefStore(conn, account_id=1)
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        refs.ensure_peer_in_tx("user", 5, display_name="X", username=None)
        raise RuntimeError("abort")
    assert refs.peer_by_identity("user:5") is None


def test_ensure_peer_in_tx_refuses_outside_a_transaction(conn):
    with pytest.raises(ValueError, match="transaction"):
        RefStore(conn, account_id=1).ensure_peer_in_tx("user", 5, display_name=None, username=None)


def test_write_epoch_state_rolls_back_with_its_transaction(conn):
    state = bind_epoch_state(conn)
    set_locked(state, True, now=NOW)
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        write_epoch_state(conn, state)
        raise RuntimeError("abort")
    assert conn.execute("SELECT locked FROM security_state").fetchone()[0] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_phase5a_primitives.py -q`
Expected: FAIL with `AttributeError` / `ImportError` (`EVENT_COLUMNS`, `insert_checkpoint`, `latch_degraded`, `ensure_peer_in_tx`, `write_epoch_state` do not exist).

- [ ] **Step 3: Implement**

In `chain.py`:
- Rename `_EVENT_COLUMNS` to `EVENT_COLUMNS` and replace every use in the file.
- Keep `_EVENT_COLUMNS = EVENT_COLUMNS` for one release **only if** `grep -rn _EVENT_COLUMNS src tests` shows another user. Otherwise do not keep it.
- Add both names to `__all__`, and add:

```python
import threading

# One process-wide guard for every audit append (design §2.1, 0B G5): the
# coordinator's disclosure barrier and every audited admin command take this
# same lock, so the chain cannot fork between them.
APPEND_GUARD = threading.Lock()
```

Split `write_checkpoint`. The existing body up to and including the `INSERT` becomes `insert_checkpoint`, and `write_checkpoint` keeps its old contract:

```python
def insert_checkpoint(
    conn: sqlite3.Connection, checkpoint_key: bytes, *, now: str
) -> dict[str, Any]:
    """Sign the current head inside the caller's transaction. Never commits."""
    require_immediate_transaction(conn)
    # ... the existing body of write_checkpoint, unchanged, minus conn.commit() ...
    return row


def write_checkpoint(
    conn: sqlite3.Connection, checkpoint_key: bytes, *, now: str
) -> dict[str, Any]:
    """Compatibility wrapper: one checkpoint in its own transaction."""
    with immediate_transaction(conn):
        return insert_checkpoint(conn, checkpoint_key, now=now)
```

`require_immediate_transaction` raises `ChainError("append_event requires …")`. Change its message to the neutral `"an open BEGIN IMMEDIATE transaction is required"`, then run `grep -rn "append_event requires" tests` and update any test that matches the old text.

In `anchor.py`, add the following and add it to `__all__`:

```python
def latch_degraded(
    conn: sqlite3.Connection, *, reason: str, disclosure_ref: str = ""
) -> None:
    """The single degraded latch (design §6.8). Set only, never cleared here."""
    from telegram_mcp.storage.settings import set_setting

    set_setting(conn, "audit.integrity_degraded", 1)
    set_setting(conn, "audit.degraded_disclosure_ref", disclosure_ref)
    set_setting(conn, "audit.degraded_reason", reason)
```

In `coordinator.py`:
- Delete `_APPEND_GUARD = threading.Lock()` and add `from telegram_mcp.disclosure.audit.chain import APPEND_GUARD`.
- Replace `with _APPEND_GUARD:` with `with APPEND_GUARD:`.
- Replace the body of `_latch_degraded` with `latch_degraded(self._conn, reason=reason, disclosure_ref=disclosure_ref)`, importing `latch_degraded` from `anchor`.
- Remove `import threading` if it is now unused.

In `refstore.py`, split `ensure_peer`:

```python
    def ensure_peer_in_tx(
        self, peer_type: str, peer_id: int, *, display_name: str | None, username: str | None
    ) -> PeerRow:
        """Upsert inside the caller's transaction (design §2.1). Never commits."""
        if not self._conn.in_transaction:
            raise ValueError("ensure_peer_in_tx requires an open transaction")
        if peer_type not in PEER_TYPES:
            raise ValueError("unknown peer type")
        now = _now()
        # ... the existing SELECT / INSERT / UPDATE block, unchanged ...
        found = self.peer_by_identity(f"{peer_type}:{peer_id}")
        assert found is not None
        return found

    def ensure_peer(
        self, peer_type: str, peer_id: int, *, display_name: str | None, username: str | None
    ) -> PeerRow:
        with immediate_transaction(self._conn):
            return self.ensure_peer_in_tx(
                peer_type, peer_id, display_name=display_name, username=username
            )
```

In `db.py`, split `save_epoch_state`:

```python
def write_epoch_state(conn: sqlite3.Connection, state: Mapping[str, Any]) -> None:
    """The UPDATEs of :func:`save_epoch_state`, inside the caller's transaction."""
    require_foreign_keys(conn)
    if not conn.in_transaction:
        raise StorageError("write_epoch_state requires an open transaction")
    security = state["security_state"]
    # ... the three existing UPDATE blocks, unchanged ...


def save_epoch_state(conn: sqlite3.Connection, state: Mapping[str, Any]) -> None:
    """Write epoch state back transactionally (spec §10.6, §10.8, §12.2)."""
    conn.commit()
    try:
        conn.execute("BEGIN")
        write_epoch_state(conn, state)
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
```

`StorageError` subclasses `Exception` (`db.py:70`, checked), so the guard raises `StorageError`, the module's own error type. No test pins that branch.

- [ ] **Step 4: Run to verify they pass, then the whole suite**

Run: `uv run pytest tests/unit/test_phase5a_primitives.py -q && uv run pytest -q`
Expected: 7 passed; then the full suite `1344 passed, 10 skipped` (1337 + 7). Every "full suite" count in this plan is the previous task's count plus that task's new tests. If a count differs, list the added and removed tests against this plan before continuing. Never shrug off a count difference.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/audit/chain.py src/telegram_mcp/disclosure/audit/anchor.py \
  src/telegram_mcp/disclosure/coordinator.py src/telegram_mcp/storage/refstore.py \
  src/telegram_mcp/storage/db.py tests/unit/test_phase5a_primitives.py
git commit -m "refactor: make audit and storage primitives composable in a caller transaction"
```

---

### Task 2: The handler runners

**Files:**
- Create: `src/telegram_mcp/ipc/handlers/_wrapper.py`
- Test: `tests/unit/test_handler_wrapper.py`

**Interfaces:**
- Consumes (Task 1): `APPEND_GUARD`, `EVENT_COLUMNS`, `append_event`, `immediate_transaction`, `mint_event_id`, `latch_degraded`, `write_anchor`, `AnchorError`.
- Produces:
  - `TxCommand(parse, plan, apply, post_commit=None)`
  - `Handler = Callable[[dict[str, Any]], dict[str, Any]]`
  - `run_tx(conn, command, args) -> dict`
  - `tx_handler(conn, command) -> Handler`
  - `simulate_tx(conn, command, args, observe) -> tuple[T, T]`
  - `AuditSink(chain_key: bytes, anchor_path: Path, now: Callable[[], str])`
  - `admin_event(tool_name: str, now: str, **fields) -> dict`
  - `run_audited_tx(conn, sink, command, args, *, event, allow_degraded=False) -> dict`, where `event: Callable[[Any, dict], dict] | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_handler_wrapper.py
"""The only place an admin transaction commits (design §2.1, 0B G4, G5, G12)."""

import os

import pytest

from telegram_mcp.disclosure.audit.anchor import CLEAN, derive_integrity
from telegram_mcp.disclosure.audit.chain import verify_chain
from telegram_mcp.ipc.handlers._wrapper import (
    AuditSink,
    TxCommand,
    admin_event,
    run_audited_tx,
    run_tx,
    simulate_tx,
)
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.settings import get_setting
from tests.authority_fixtures import seed_authority_rows

KEY = b"k" * 32
NOW = "2026-09-24T00:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    return c


def _rename(name="Renamed", seen=None):
    def plan(conn, parsed):
        if seen is not None:
            seen.append(conn.in_transaction)
        return parsed

    def apply(conn, plan):
        conn.execute("UPDATE projects SET display_name = ?", (plan["name"],))
        return {"name": plan["name"]}

    return TxCommand(parse=lambda args: {"name": args.get("name", name)}, plan=plan, apply=apply)


def _name(conn):
    return conn.execute("SELECT display_name FROM projects").fetchone()[0]


def test_plan_runs_inside_the_transaction_and_run_tx_commits(conn):
    seen: list[bool] = []
    assert run_tx(conn, _rename(seen=seen), {}) == {"name": "Renamed"}
    assert seen == [True]
    assert _name(conn) == "Renamed" and not conn.in_transaction


def test_presence_never_reaches_parse(conn):
    got = {}
    cmd = TxCommand(
        parse=lambda args: got.update(args) or {}, plan=lambda c, p: p, apply=lambda c, p: {}
    )
    run_tx(conn, cmd, {"presence": {"token": "t"}, "x": 1})
    assert got == {"x": 1}


def test_an_apply_failure_rolls_everything_back(conn):
    def apply(conn, plan):
        conn.execute("UPDATE projects SET display_name = 'half'")
        raise ValueError("refused")

    with pytest.raises(ValueError, match="refused"):
        run_tx(conn, TxCommand(lambda a: {}, lambda c, p: p, apply), {})
    assert _name(conn) == "Alpha" and not conn.in_transaction


def test_a_post_commit_failure_never_undoes_the_commit(conn):
    def boom(result):
        raise RuntimeError("cache eviction failed")

    cmd = _rename()
    cmd = TxCommand(cmd.parse, cmd.plan, cmd.apply, post_commit=boom)
    assert run_tx(conn, cmd, {}) == {"name": "Renamed"}
    assert _name(conn) == "Renamed"


def test_simulate_observes_the_change_and_leaves_no_trace(conn):
    before, after = simulate_tx(conn, _rename(), {}, _name)
    assert (before, after) == ("Alpha", "Renamed")
    assert _name(conn) == "Alpha"


def test_simulate_never_leaves_a_transaction_open(conn):
    def apply(conn, plan):
        raise ValueError("refused")

    with pytest.raises(ValueError):
        simulate_tx(conn, TxCommand(lambda a: {}, lambda c, p: p, apply), {}, _name)
    assert not conn.in_transaction
    assert run_tx(conn, _rename(), {}) == {"name": "Renamed"}  # connection still usable


@pytest.fixture
def sink(tmp_path):
    anchor_dir = tmp_path / "anchor"
    anchor_dir.mkdir(mode=0o700)
    return AuditSink(chain_key=KEY, anchor_path=anchor_dir / "anchor.json", now=lambda: NOW)


def test_an_audited_command_appends_and_refreshes_the_anchor(conn, sink):
    result = run_audited_tx(
        conn, sink, _rename(), {}, event=lambda plan, result: admin_event("admin.lock", NOW)
    )
    assert result == {"name": "Renamed", "anchor": "refreshed"}
    verify_chain(conn, KEY)
    assert derive_integrity(conn, KEY, sink.anchor_path) == CLEAN


def test_an_anchor_failure_keeps_the_commit_and_latches_degraded(conn, tmp_path):
    broken = AuditSink(KEY, tmp_path / "missing-dir" / "anchor.json", lambda: NOW)
    result = run_audited_tx(
        conn, broken, _rename(), {}, event=lambda p, r: admin_event("admin.lock", NOW)
    )
    assert result["anchor"] == "degraded" and _name(conn) == "Renamed"
    assert get_setting(conn, "audit.integrity_degraded") == 1


def test_audited_commands_refuse_while_degraded(conn, sink):
    from telegram_mcp.disclosure.audit.anchor import latch_degraded

    latch_degraded(conn, reason="anchor_refresh_failure")
    with pytest.raises(PermissionError):
        run_audited_tx(conn, sink, _rename(), {}, event=lambda p, r: admin_event("admin.lock", NOW))
    assert _name(conn) == "Alpha"


def test_a_busy_database_is_a_fixed_refusal(conn, tmp_path):
    from telegram_mcp.ipc.handlers._wrapper import BUSY

    holder = open_db(tmp_path / "m.db")
    holder.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA busy_timeout = 50")
    try:
        with pytest.raises(ValueError, match=BUSY):
            run_tx(conn, _rename(), {})
        with pytest.raises(ValueError, match=BUSY):
            simulate_tx(conn, _rename(), {}, _name)
        assert not conn.in_transaction
    finally:
        holder.rollback()
    assert run_tx(conn, _rename(), {}) == {"name": "Renamed"}


def test_admin_event_has_every_column_and_a_closed_tool_name():
    from telegram_mcp.disclosure.audit.chain import EVENT_COLUMNS

    event = admin_event("admin.unlock", NOW)
    assert set(event) == set(EVENT_COLUMNS) and event["status"] == "ok"
    with pytest.raises(ValueError):
        admin_event("telegram_status", NOW)


def test_the_anchor_directory_mode_is_what_read_anchor_demands(sink):
    assert oct(os.stat(sink.anchor_path.parent).st_mode & 0o777) == "0o700"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_handler_wrapper.py -q`
Expected: FAIL with `ModuleNotFoundError: telegram_mcp.ipc.handlers._wrapper`.

- [ ] **Step 3: Implement**

```python
# src/telegram_mcp/ipc/handlers/_wrapper.py
"""The only place an admin transaction commits (Phase-5 design §2.1).

``_transaction`` is ``chain.immediate_transaction`` plus one thing: lock
contention on ``BEGIN IMMEDIATE`` becomes the fixed ``ValueError(BUSY)``
instead of an ``OperationalError`` the router would log as a crash.

Three handler kinds share these runners:

* ``TxCommand`` via :func:`run_tx` — pure policy mutations.
* ``TxCommand`` via :func:`run_audited_tx` — commands that append an
  ``ADMIN_EVENTS`` event. They take the coordinator's own append guard,
  refresh the external anchor after commit, and latch degraded if that
  refresh fails, exactly as disclosure step 12 does.
* Flow handlers — async functions elsewhere that do network or file I/O
  *between* calls to these runners, never inside one.

``plan`` runs inside the transaction, so it resolves refs and handles
against the state ``apply`` will write (0B G4: no TOCTOU). ``post_commit``
is cleanup only: every cached handle is bound to an epoch the commit already
moved, so a failed eviction cannot revive anything.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from telegram_mcp.disclosure.audit.anchor import AnchorError, latch_degraded, write_anchor
from telegram_mcp.disclosure.audit.chain import (
    ADMIN_EVENTS,
    APPEND_GUARD,
    EVENT_COLUMNS,
    append_event,
    mint_event_id,
)
from telegram_mcp.storage.settings import get_setting

BUSY = "the database is busy; retry"

__all__ = [
    "BUSY",
    "AuditSink",
    "Handler",
    "TxCommand",
    "admin_event",
    "run_audited_tx",
    "run_tx",
    "simulate_tx",
    "tx_handler",
]

_logger = logging.getLogger("telegram_mcp.admin")

P = TypeVar("P")
Q = TypeVar("Q")
T = TypeVar("T")

Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class TxCommand(Generic[P, Q]):
    parse: Callable[[dict[str, Any]], P]
    plan: Callable[[sqlite3.Connection, P], Q]
    apply: Callable[[sqlite3.Connection, Q], dict[str, Any]]
    post_commit: Callable[[dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class AuditSink:
    chain_key: bytes
    anchor_path: Path
    now: Callable[[], str]


def _bare(args: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k != "presence"}


def _cleanup(command: TxCommand[Any, Any], result: dict[str, Any]) -> None:
    if command.post_commit is None:
        return
    try:
        command.post_commit(result)
    except Exception:
        _logger.exception("admin post-commit cleanup failed")


def _busy(exc: sqlite3.OperationalError) -> bool:
    text = str(exc)
    return "locked" in text or "busy" in text


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """``immediate_transaction``, with lock contention as a fixed refusal (Review Focus #1)."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _busy(exc):
            raise ValueError(BUSY) from None
        raise
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def run_tx(
    conn: sqlite3.Connection, command: TxCommand[Any, Any], args: Mapping[str, Any]
) -> dict[str, Any]:
    parsed = command.parse(_bare(args))
    with _transaction(conn):
        result = command.apply(conn, command.plan(conn, parsed))
    _cleanup(command, result)
    return result


def tx_handler(conn: sqlite3.Connection, command: TxCommand[Any, Any]) -> Handler:
    return lambda args: run_tx(conn, command, args)


def simulate_tx(
    conn: sqlite3.Connection,
    command: TxCommand[Any, Any],
    args: Mapping[str, Any],
    observe: Callable[[sqlite3.Connection], T],
) -> tuple[T, T]:
    """Run the real plan and apply, observe, and roll everything back (0B G12)."""
    parsed = command.parse(_bare(args))
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _busy(exc):
            raise ValueError(BUSY) from None
        raise
    try:
        before = observe(conn)
        conn.execute("SAVEPOINT simulation")
        try:
            command.apply(conn, command.plan(conn, parsed))
            after = observe(conn)
        finally:
            conn.execute("ROLLBACK TO simulation")
            conn.execute("RELEASE simulation")
    finally:
        conn.rollback()
    return before, after


def admin_event(tool_name: str, now: str, **fields: Any) -> dict[str, Any]:
    """A privacy-minimised chain event for the closed admin vocabulary."""
    if tool_name not in ADMIN_EVENTS:
        raise ValueError("not an admin event name")
    event: dict[str, Any] = dict.fromkeys(EVENT_COLUMNS)
    event.update(event_id=mint_event_id(), ts=now, tool_name=tool_name, status="ok")
    unknown = set(fields) - set(EVENT_COLUMNS)
    if unknown:
        raise ValueError("unknown event column")
    event.update(fields)
    return event


def run_audited_tx(
    conn: sqlite3.Connection,
    sink: AuditSink,
    command: TxCommand[Any, Any],
    args: Mapping[str, Any],
    *,
    event: Callable[[Any, dict[str, Any]], dict[str, Any]] | None,
    allow_degraded: bool = False,
) -> dict[str, Any]:
    parsed = command.parse(_bare(args))
    with APPEND_GUARD:
        if get_setting(conn, "audit.integrity_degraded") and not allow_degraded:
            raise PermissionError("audit integrity is degraded")
        appended: dict[str, Any] | None = None
        with _transaction(conn):
            plan = command.plan(conn, parsed)
            result = command.apply(conn, plan)
            if event is not None:
                appended = append_event(conn, sink.chain_key, event(plan, result))
        if appended is not None:
            try:
                write_anchor(
                    sink.anchor_path,
                    sink.chain_key,
                    now=sink.now(),
                    chain_epoch=appended["chain_epoch"],
                    chain_seq=appended["chain_seq"],
                    event_id=appended["event_id"],
                    event_mac=appended["event_mac"],
                )
            except (AnchorError, OSError, RuntimeError):
                latch_degraded(conn, reason="anchor_refresh_failure")
                result = {**result, "anchor": "degraded"}
            else:
                result = {**result, "anchor": "refreshed"}
    _cleanup(command, result)
    return result
```

`ADMIN_EVENTS` is defined in `chain.py:48` but is **not** in its `__all__`. Checked at `6680090`. Add `"ADMIN_EVENTS"`, `"APPEND_GUARD"`, `"EVENT_COLUMNS"` and `"insert_checkpoint"` to `chain.__all__` in Task 1.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_handler_wrapper.py -q && uv run pytest -q`
Expected: 12 passed; full suite `1356 passed, 10 skipped`.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/ipc/handlers/_wrapper.py tests/unit/test_handler_wrapper.py
git commit -m "feat: add the admin transaction runners (tx, audited tx, simulation)"
```

---

### Task 3: Move the existing handlers onto the runners, and guard the boundary

Five changes land here:

- `projects.py`, `scope.py` and `clients.py` become `TxCommand`s.
- The duplicate `scope mode` (`projects.py:196` and `scope.py:123`) collapses to one copy in `projects.py`, keyed by the owner row.
- `scope allow` / `scope deny` / `project add-peer` stop writing `peers` outside their transaction.
- `client rotate` stops re-enabling a disabled client and fsyncs its seed before the TX (0B G11).
- Two AST guards land.

**Files:**
- Rewrite: `src/telegram_mcp/ipc/handlers/projects.py`
- Rewrite: `src/telegram_mcp/ipc/handlers/scope.py`
- Rewrite: `src/telegram_mcp/ipc/handlers/clients.py`
- Modify: `tests/unit/test_scope_handlers.py:61` (`scope mode` now comes from `project_handlers`)
- Test: `tests/unit/test_phase5a_handlers.py`, `tests/security/test_phase5a_architecture.py`

**Interfaces:**
- Consumes (Task 2): `TxCommand`, `run_tx`, `tx_handler`.
- Produces:
  - `projects.PROJECT_COMMANDS: dict[str, TxCommand]`, with keys `project create|enable|disable|grant-client|set-egress|revoke-client` and `scope mode`
  - `projects.project_handlers(conn) -> dict[str, Handler]`
  - `scope.scope_commands(discovery) -> dict[str, TxCommand]`, with keys `scope allow|deny` and `project add-peer`
  - `scope.scope_handlers(conn, session, discovery)`
  - `clients.client_handlers(conn, *, key_dir)`
  - `clients.CLIENT_COMMANDS: dict[str, TxCommand]`, empty in this task and filled by Task 9

- [ ] **Step 1: Write the failing tests**

```python
# tests/security/test_phase5a_architecture.py
"""Design §2.1 and 0B G4: who may own a transaction, and no await inside one."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "telegram_mcp"
HANDLERS = SRC / "ipc" / "handlers"
TX_NAMES = {"immediate_transaction", "commit", "rollback"}


def _names(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
    return found


def test_only_the_wrapper_owns_an_admin_transaction():
    for path in sorted(HANDLERS.glob("*.py")):
        if path.name == "_wrapper.py":
            continue
        assert not _names(ast.parse(path.read_text())) & TX_NAMES, path


def _is_tx(expr: ast.expr) -> bool:
    func = expr.func if isinstance(expr, ast.Call) else None
    name = getattr(func, "attr", None) or getattr(func, "id", None)
    return name == "immediate_transaction"


def test_no_await_inside_a_transaction():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                _is_tx(item.context_expr) for item in node.items
            ):
                for inner in (n for stmt in node.body for n in ast.walk(stmt)):
                    if isinstance(inner, ast.Await):
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_scope_mode_exists_exactly_once():
    hits = [
        p.name
        for p in HANDLERS.glob("*.py")
        if '"scope mode"' in p.read_text()
    ]
    assert hits == ["projects.py"]
```

```python
# tests/unit/test_phase5a_handlers.py
"""Handlers on the runners: behaviour kept, two shipped defects fixed (0B G11)."""

import os
import stat

import pytest

from telegram_mcp.ipc.handlers.clients import client_handlers
from telegram_mcp.ipc.handlers.projects import PROJECT_COMMANDS, project_handlers
from telegram_mcp.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    return c


def test_scope_mode_is_keyed_to_the_owner_row(conn):
    h = project_handlers(conn)
    before = conn.execute("SELECT policy_epoch FROM policy_state").fetchone()[0]
    assert h["scope mode"]({"mode": "all_cloud_chats"}) == {"mode": "all_cloud_chats"}
    row = conn.execute("SELECT mode, policy_epoch FROM policy_state").fetchone()
    assert tuple(row) == ("all_cloud_chats", before + 1)


def test_every_project_command_is_a_tx_command():
    assert set(PROJECT_COMMANDS) == {
        "project create",
        "project enable",
        "project disable",
        "project grant-client",
        "project set-egress",
        "project revoke-client",
        "scope mode",
    }


def test_rotate_refuses_a_disabled_client_without_enable(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    ref = h["client rotate"]({"client": "claude_code_local"})["client_ref"]
    conn.execute("UPDATE mcp_clients SET enabled = 0 WHERE client_ref = ?", (ref,))
    conn.commit()
    seed = (keys / f"lease-seed.{ref}").read_bytes()
    with pytest.raises(ValueError, match="disabled"):
        h["client rotate"]({"client": "claude_code_local"})
    assert (keys / f"lease-seed.{ref}").read_bytes() == seed  # refused before any write
    assert h["client rotate"]({"client": "claude_code_local", "enable": True})["client_ref"] == ref
    enabled = conn.execute("SELECT enabled FROM mcp_clients WHERE client_ref = ?", (ref,))
    assert enabled.fetchone()[0] == 1


def test_a_refused_rotation_leaves_the_live_seed_untouched(conn, tmp_path):
    """Review #10: an error must not have a side effect."""
    from telegram_mcp.ipc.handlers._wrapper import BUSY

    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    ref = h["client rotate"]({"client": "codex_local"})["client_ref"]
    live = (keys / f"lease-seed.{ref}").read_bytes()
    holder = open_db(tmp_path / "m.db")
    holder.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA busy_timeout = 50")
    try:
        with pytest.raises(ValueError, match=BUSY):
            h["client rotate"]({"client": "codex_local"})
    finally:
        holder.rollback()
    assert (keys / f"lease-seed.{ref}").read_bytes() == live
    assert not (keys / f"lease-seed.{ref}.next").exists()


def test_a_successful_rotation_activates_a_new_seed(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    ref = h["client rotate"]({"client": "codex_local"})["client_ref"]
    first = (keys / f"lease-seed.{ref}").read_bytes()
    assert h["client rotate"]({"client": "codex_local"})["seed"] == "activated"
    assert (keys / f"lease-seed.{ref}").read_bytes() != first
    assert not (keys / f"lease-seed.{ref}.next").exists()


def test_list_commands_reject_arguments(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        client_handlers(conn, key_dir=keys)["client list"]({"x": 1})
    with pytest.raises(ValueError):
        project_handlers(conn)["project list"]({"x": 1})


def test_rotated_seeds_are_0600(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    ref = client_handlers(conn, key_dir=keys)["client rotate"]({"client": "codex_local"})[
        "client_ref"
    ]
    assert stat.S_IMODE(os.stat(keys / f"lease-seed.{ref}").st_mode) == 0o600
```

Also add this to `tests/unit/test_scope_handlers.py`. It is Review Focus #2:

```python
async def test_a_failed_eviction_still_refuses_stale_handles(world, monkeypatch):
    conn, h = world
    found = await h["scope discover"]({})
    first, second = found["selections"][0]["handle"], found["selections"][1]["handle"]
    from telegram_mcp.telegram.discovery import DiscoveryStore

    monkeypatch.setattr(DiscoveryStore, "invalidate_all", lambda self: None)  # eviction "fails"
    h["scope allow"]({"handle": first})
    with pytest.raises(ValueError):
        h["scope deny"]({"handle": second})  # still refused: the policy epoch moved
```

In the same file, change `test_scope_mode_bumps_the_epoch_and_refuses_unknown_modes` to call `project_handlers(conn)["scope mode"]` (import it from `telegram_mcp.ipc.handlers.projects`) instead of `h["scope mode"]`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/security/test_phase5a_architecture.py tests/unit/test_phase5a_handlers.py tests/unit/test_scope_handlers.py -q`
Expected: FAIL.
- `test_only_the_wrapper_owns_an_admin_transaction` names `projects.py`, `scope.py` and `clients.py`.
- `test_scope_mode_exists_exactly_once` gets `["projects.py", "scope.py"]`.
- `PROJECT_COMMANDS` cannot be imported.
- The rotate test fails because rotation re-enables.

- [ ] **Step 3: Implement `projects.py`**

Keep the module docstring's first paragraph. Keep `_SLUG`, `_EGRESS`, `_MODES`, `PROMPT_UNSAFE`, `_now`, `_args`, `_display_name`, `_egress` and `_id` unchanged. Replace everything from `def project_handlers` down with:

```python
def _parse_create(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"slug", "display_name"})
    slug = body.get("slug")
    if not isinstance(slug, str) or _SLUG.fullmatch(slug) is None:
        raise ValueError("slug must match [a-z0-9][a-z0-9-]{0,31}")
    return {"slug": slug, "name": _display_name(body.get("display_name"))}


def _plan_create(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    accounts = conn.execute("SELECT id FROM accounts").fetchall()
    if len(accounts) != 1:
        raise ValueError("exactly one account is required")
    return {**parsed, "account_id": int(accounts[0][0])}


def _apply_create(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    ref = mint_opaque_ref("tpr_")
    now = _now()
    try:
        conn.execute(
            "INSERT INTO projects (account_id, project_ref, slug, display_name, enabled,"
            " project_epoch, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?, ?)",
            (plan["account_id"], ref, plan["slug"], plan["name"], now, now),
        )
    except sqlite3.IntegrityError:
        raise ValueError("slug already exists") from None
    return {"project_ref": ref}


def _enabled(value: int) -> TxCommand[dict[str, Any], dict[str, Any]]:
    def plan(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": _id(conn, "projects", "project_ref", parsed.get("project_ref"))}

    def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        try:
            conn.execute(
                "UPDATE projects SET enabled = ?, project_epoch = project_epoch + 1,"
                " updated_at = ? WHERE id = ?",
                (value, _now(), plan["project_id"]),
            )
        except sqlite3.IntegrityError:
            raise ValueError("enabling would activate an undeclared shared membership") from None
        return {"enabled": bool(value)}

    return TxCommand(lambda args: _args(args, {"project_ref"}), plan, apply)


def _pair(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        **parsed,
        "project_id": _id(conn, "projects", "project_ref", parsed.get("project_ref")),
        "client_id": _id(conn, "mcp_clients", "client_ref", parsed.get("client_ref")),
    }


def _parse_grant(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(
        args,
        {"project_ref", "client_ref", "egress_level", "excerpt_max_codepoints", "can_cross_search"},
    )
    level, excerpt = _egress(body)
    cross = body.get("can_cross_search", False)
    if not isinstance(cross, bool):
        raise ValueError("can_cross_search must be a boolean")  # noqa: TRY004 -- uniform ValueError on admin validation
    return {**body, "level": level, "excerpt": excerpt, "cross": cross}


def _apply_grant(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    try:
        conn.execute(
            "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
            " egress_level, excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?, ?, ?, ?)"
            " ON CONFLICT(client_id, project_id) DO UPDATE SET can_read = 1,"
            " can_cross_search = excluded.can_cross_search, egress_level = excluded.egress_level,"
            " excerpt_max_codepoints = excluded.excerpt_max_codepoints, updated_at = excluded.updated_at",
            (
                plan["client_id"],
                plan["project_id"],
                int(plan["cross"]),
                plan["level"],
                plan["excerpt"],
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        raise ValueError("grant refused by the owner-consistency rules") from None
    return {"granted": True}


def _parse_egress(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"project_ref", "client_ref", "egress_level", "excerpt_max_codepoints"})
    level, excerpt = _egress(body)
    return {**body, "level": level, "excerpt": excerpt}


def _apply_egress(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    changed = conn.execute(
        "UPDATE client_projects SET egress_level = ?, excerpt_max_codepoints = ?, updated_at = ?"
        " WHERE client_id = ? AND project_id = ?",
        (plan["level"], plan["excerpt"], _now(), plan["client_id"], plan["project_id"]),
    ).rowcount
    if changed != 1:
        raise ValueError("no such grant")
    return {"egress_level": plan["level"]}


def _apply_revoke(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    removed = conn.execute(
        "DELETE FROM client_projects WHERE client_id = ? AND project_id = ?",
        (plan["client_id"], plan["project_id"]),
    ).rowcount
    if removed != 1:
        raise ValueError("no such grant")
    return {"revoked": True}


def _parse_mode(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"mode"})
    if body.get("mode") not in _MODES:
        raise ValueError("mode must be allowlist or all_cloud_chats")
    return body


def _plan_owner(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute("SELECT principal_id, account_id FROM policy_state").fetchall()
    if len(rows) != 1:
        raise ValueError("exactly one owner policy row is required")
    return {**parsed, "principal_id": int(rows[0][0]), "account_id": int(rows[0][1])}


def _apply_mode(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        "UPDATE policy_state SET mode = ?, policy_epoch = policy_epoch + 1, updated_at = ?"
        " WHERE principal_id = ? AND account_id = ?",
        (plan["mode"], _now(), plan["principal_id"], plan["account_id"]),
    )
    return {"mode": plan["mode"]}


PROJECT_COMMANDS: dict[str, TxCommand[Any, Any]] = {
    "project create": TxCommand(_parse_create, _plan_create, _apply_create),
    "project enable": _enabled(1),
    "project disable": _enabled(0),
    "project grant-client": TxCommand(_parse_grant, _pair, _apply_grant),
    "project set-egress": TxCommand(_parse_egress, _pair, _apply_egress),
    "project revoke-client": TxCommand(
        lambda args: _args(args, {"project_ref", "client_ref"}), _pair, _apply_revoke
    ),
    "scope mode": TxCommand(_parse_mode, _plan_owner, _apply_mode),
}


def project_handlers(conn: sqlite3.Connection) -> dict[str, Handler]:
    def list_(args: dict[str, Any]) -> dict[str, Any]:
        _args(args, set())
        rows = conn.execute(
            "SELECT project_ref, slug, display_name, enabled, project_epoch FROM projects"
            " ORDER BY slug"
        ).fetchall()
        return {
            "projects": [
                {
                    "project_ref": r[0],
                    "slug": r[1],
                    "display_name": r[2],
                    "enabled": bool(r[3]),
                    "project_epoch": r[4],
                }
                for r in rows
            ]
        }

    return {
        **{name: tx_handler(conn, command) for name, command in PROJECT_COMMANDS.items()},
        "project list": list_,
    }
```

Replace the imports: drop `immediate_transaction` and add `from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, tx_handler`. Delete the old local `Handler = ...` alias.

- [ ] **Step 4: Implement `scope.py`**

Keep the docstring, `_DISCOVER_DEADLINE_S` and `_now`. Replace the rest with:

```python
from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, tx_handler


def _owner(conn: sqlite3.Connection) -> tuple[int, int, int]:
    row = conn.execute(
        "SELECT principal_id, account_id, policy_epoch FROM policy_state"
    ).fetchone()
    if row is None:
        raise ValueError("log in first: no account exists")
    return int(row[0]), int(row[1]), int(row[2])


def _parse_handle(args: dict[str, Any], extra: frozenset[str] = frozenset()) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - {"handle", *extra}:
        raise ValueError("unknown argument")
    handle = body.get("handle")
    if not isinstance(handle, str) or not handle.startswith("tgl_"):
        raise ValueError("handle must be a tgl_ selection from scope discover")
    return body


def scope_commands(discovery: DiscoveryStore) -> dict[str, TxCommand[Any, Any]]:
    def plan_selection(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        principal, account, epoch = _owner(conn)
        view = discovery.take(parsed["handle"], policy_epoch=epoch)
        return {**parsed, "principal": principal, "account": account, "view": view}

    def decide(decision: str) -> TxCommand[Any, Any]:
        def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
            view = plan["view"]
            RefStore(conn, account_id=plan["account"]).ensure_peer_in_tx(
                view.peer_type, view.peer_id, display_name=view.display_name, username=view.username
            )
            now = _now()
            conn.execute(
                "INSERT INTO peer_policy (principal_id, account_id, telegram_peer_type,"
                " telegram_peer_id, decision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(principal_id, account_id, telegram_peer_type, telegram_peer_id)"
                " DO UPDATE SET decision = excluded.decision, updated_at = excluded.updated_at",
                (plan["principal"], plan["account"], view.peer_type, view.peer_id, decision, now, now),
            )
            conn.execute(
                "UPDATE policy_state SET policy_epoch = policy_epoch + 1, updated_at = ?"
                " WHERE principal_id = ? AND account_id = ?",
                (now, plan["principal"], plan["account"]),
            )
            return {"decision": decision}

        return TxCommand(
            _parse_handle, plan_selection, apply, post_commit=lambda _r: discovery.invalidate_all()
        )

    def parse_add(args: dict[str, Any]) -> dict[str, Any]:
        body = _parse_handle(args, frozenset({"project_ref", "shared"}))
        if not isinstance(body.get("shared", False), bool):
            raise ValueError("shared must be a boolean")  # noqa: TRY004 -- uniform ValueError on admin validation
        return body

    def plan_add(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        plan = plan_selection(conn, parsed)
        project = conn.execute(
            "SELECT id FROM projects WHERE project_ref = ? AND account_id = ?",
            (parsed.get("project_ref"), plan["account"]),
        ).fetchone()
        if project is None:
            raise ValueError("unknown project_ref")
        return {**plan, "project_id": int(project[0])}

    def apply_add(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        view = plan["view"]
        peer = RefStore(conn, account_id=plan["account"]).ensure_peer_in_tx(
            view.peer_type, view.peer_id, display_name=view.display_name, username=view.username
        )
        now = _now()
        try:
            conn.execute(
                "INSERT INTO project_peers (project_id, peer_id, membership_kind, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?)",
                (plan["project_id"], peer.row_id, "shared" if plan.get("shared") else "primary", now, now),
            )
            conn.execute(
                "UPDATE projects SET project_epoch = project_epoch + 1, updated_at = ? WHERE id = ?",
                (now, plan["project_id"]),
            )
        except sqlite3.IntegrityError:
            raise ValueError(
                "membership refused: overlapping membership must be declared shared"
            ) from None
        return {"added": True}

    return {
        "scope allow": decide("allow"),
        "scope deny": decide("deny"),
        "project add-peer": TxCommand(parse_add, plan_add, apply_add),
    }


def scope_handlers(
    conn: sqlite3.Connection, session: Any, discovery: DiscoveryStore
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def discover(args: dict[str, Any]) -> dict[str, Any]:
        _principal, _account, epoch = _owner(conn)
        views, complete = await session.scan_dialogs(
            client_ref="operator", deadline=Deadline(_DISCOVER_DEADLINE_S), budget=WorkBudget()
        )
        return {
            "selections": discovery.new_snapshot(views, policy_epoch=epoch),
            "complete": complete,
        }

    def list_(args: dict[str, Any]) -> dict[str, Any]:
        if {k for k in args if k != "presence"}:
            raise ValueError("unknown argument")
        rows = conn.execute(
            "SELECT pp.decision, p.display_name_cache, p.telegram_peer_type FROM peer_policy pp"
            " LEFT JOIN peers p ON p.account_id = pp.account_id"
            " AND p.telegram_peer_type = pp.telegram_peer_type"
            " AND p.telegram_peer_id = pp.telegram_peer_id ORDER BY pp.decision, p.display_name_cache"
        ).fetchall()
        return {
            "rules": [{"decision": r[0], "display_name": r[1], "peer_type": r[2]} for r in rows]
        }

    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        name: tx_handler(conn, command) for name, command in scope_commands(discovery).items()
    }
    handlers.update({"scope discover": discover, "scope list": list_})
    return handlers
```

Drop the now-unused `immediate_transaction` import. `Handler` is unused here, so import only `TxCommand, tx_handler`.

- [ ] **Step 5: Implement `clients.py`**

```python
"""Coding-client credentials (spec §9.7): rotate and disable.

A refused or failed rotation changes nothing (review #10). The new seed is
written and fsynced to ``lease-seed.<ref>.next`` first. The transaction then
records the rotation. Only after it commits does the pending seed replace
the live one. If the transaction fails, the pending file is deleted and the
live seed is untouched. A crash after commit but before activation leaves
the old seed live and a stale ``.next`` behind, which the next rotation
overwrites and 5b's doctor reports. Rotation never re-enables a disabled
client unless the operator says ``enable``.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, run_tx
from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["CLIENT_COMMANDS", "client_handlers"]

_KINDS = ("codex_local", "claude_code_local")

CLIENT_COMMANDS: dict[str, TxCommand[Any, Any]] = {}


def _fsync_dir(key_dir: Path) -> None:
    directory = os.open(key_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_pending_seed(key_dir: Path, client_ref: str) -> Path:
    pending = key_dir / f"lease-seed.{client_ref}.next"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secrets.token_bytes(32))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(pending, 0o600)
    _fsync_dir(key_dir)
    return pending


def _activate_seed(key_dir: Path, client_ref: str, pending: Path) -> None:
    os.replace(pending, key_dir / f"lease-seed.{client_ref}")
    _fsync_dir(key_dir)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def client_handlers(conn: sqlite3.Connection, *, key_dir: Path) -> dict[str, Handler]:
    def rotate(args: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in args.items() if k != "presence"}
        if set(body) - {"client", "enable"}:
            raise ValueError("unknown argument")
        kind = body.get("client")
        if kind not in _KINDS:
            raise ValueError("client must be codex_local or claude_code_local")
        enable = body.get("enable", False)
        if not isinstance(enable, bool):
            raise ValueError("enable must be a boolean")  # noqa: TRY004 -- uniform ValueError on admin validation
        row = conn.execute(
            "SELECT client_ref, enabled FROM mcp_clients WHERE client_kind = ?", (kind,)
        ).fetchone()
        if row is not None and not row[1] and not enable:
            raise ValueError("client is disabled; pass enable to re-enable it")
        ref = row[0] if row is not None else mint_opaque_ref("tcl_")
        pending = _write_pending_seed(key_dir, ref)  # durable, but not yet live

        def plan(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
            principal = conn.execute("SELECT id FROM principals ORDER BY id LIMIT 1").fetchone()
            if principal is None:
                raise ValueError("the owner principal does not exist")
            current = conn.execute(
                "SELECT enabled FROM mcp_clients WHERE client_ref = ?", (ref,)
            ).fetchone()
            if current is not None and not current[0] and not enable:
                raise ValueError("client is disabled; pass enable to re-enable it")
            return {"principal_id": int(principal[0]), "exists": current is not None}

        def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
            now = _now()
            if plan["exists"]:
                conn.execute(
                    "UPDATE mcp_clients SET rotated_at = ?, enabled = 1 WHERE client_ref = ?",
                    (now, ref),
                )
            else:
                conn.execute(
                    "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding,"
                    " client_kind, enabled, created_at) VALUES (?, ?, 'bearer', ?, ?, 1, ?)",
                    (plan["principal_id"], ref, f"lease-seed:{ref}", kind, now),
                )
            return {"client_ref": ref}

        try:
            result = run_tx(conn, TxCommand(lambda a: a, plan, apply), body)
        except BaseException:
            pending.unlink(missing_ok=True)  # refused or failed: the live seed is untouched
            raise
        try:
            _activate_seed(key_dir, ref, pending)
        except OSError:
            return {**result, "seed": "pending"}  # recorded, not yet live; rotate again
        return {**result, "seed": "activated"}

    def list_(args: dict[str, Any]) -> dict[str, Any]:
        if {k for k in args if k != "presence"}:
            raise ValueError("unknown argument")
        # body otherwise verbatim from the shipped clients.py:62-71 (callers: test_identity_bootstrap,
        # test_daemon); only bearer clients are listed.
        rows = conn.execute(
            "SELECT client_ref, client_kind, enabled FROM mcp_clients"
            " WHERE auth_kind = 'bearer' ORDER BY client_kind"
        ).fetchall()
        return {
            "clients": [
                {"client_ref": r[0], "client_kind": r[1], "enabled": bool(r[2])} for r in rows
            ]
        }

    return {"client rotate": rotate, "client list": list_}
```

- [ ] **Step 6: Run the targeted tests, then the suite**

Run: `uv run pytest tests/security/test_phase5a_architecture.py tests/unit/test_phase5a_handlers.py tests/unit/test_scope_handlers.py tests/unit/test_admin_handlers.py -q && uv run pytest -q`
Expected: all pass. The full suite is `1356 + 3 (architecture) + 7 (handlers) + 1 (scope)` = `1367 passed, 10 skipped`. Every later full-suite count in this plan is stated as a delta over the previous task's count, and each commit message records the exact number.

If `test_no_await_inside_a_transaction` names a site outside `ipc/handlers/`, that is a real finding. Stop and report it with the path and line. Do not add an allowlist.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_mcp/ipc/handlers/{projects,scope,clients}.py \
  tests/security/test_phase5a_architecture.py tests/unit/test_phase5a_handlers.py \
  tests/unit/test_scope_handlers.py
git commit -m "refactor: run admin handlers on the tx runners; one scope mode; rotate keeps disable"
```

---

### Task 4: One evaluator with a trace, and the owner class rule moved into `authority/`

**Files:**
- Modify: `src/telegram_mcp/authority/policy.py`
- Modify: `src/telegram_mcp/disclosure/seams.py:151-166` (the definition goes; the re-export stays)
- Modify: `src/telegram_mcp/storage/authority_view.py:143-158` (`load_view` sets `owner_scope`)
- Test: `tests/unit/test_policy_trace.py`; add a guard to `tests/security/test_phase5a_architecture.py`

**Interfaces:**
- Produces:
  - `policy.PeerFacts(chat_type: str | None, archived: bool | None)`, with `PeerFacts.from_stored(peer_type) -> PeerFacts`
  - `policy.OwnerScope(include_archived, include_private, include_groups, include_channels)`, with `.admits(chat_type, is_archived) -> bool` (unchanged) and `.decide(facts) -> bool | None`
  - `AuthorityRequest.facts: PeerFacts | None = None`
  - `AuthorityView.owner_scope: OwnerScope | None = None`
  - `make_view(..., owner_scope=None)`
  - `policy.TraceStep = tuple[str, str]`
  - `policy.evaluate_with_trace(view, request) -> tuple[AuthoritySnapshot | Denial, tuple[TraceStep, ...]]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_policy_trace.py
"""One decision function, two entry points (design §2.2, 0B G13, G14)."""

import random

import pytest

from telegram_mcp.authority.policy import (
    AuthorityRequest,
    ClientProjectGrant,
    ClientState,
    Denial,
    OwnerScope,
    PeerFacts,
    ProjectState,
    evaluate,
    evaluate_with_trace,
    make_view,
)


def _view(rng: random.Random):
    peers = [f"user:{i}" for i in range(4)] + ["channel:9", "chat:3"]
    projects = {
        f"p{i}": ProjectState(f"p{i}", enabled=rng.random() > 0.2, project_epoch=1)
        for i in range(3)
    }
    grants = {}
    for ref in projects:
        if rng.random() > 0.3:
            level = rng.choice(("metadata_only", "excerpt", "full_text"))
            grants[("c", ref)] = ClientProjectGrant(
                can_read=rng.random() > 0.2,
                can_cross_search=rng.random() > 0.5,
                egress_level=level,
                excerpt_limit=100 if level == "excerpt" else None,
                grant_digest=f"g{ref}",
            )
    return make_view(
        clients={"c": ClientState("c", enabled=rng.random() > 0.1, principal_ref="prn")},
        projects=projects,
        grants=grants,
        memberships={ref: set(rng.sample(peers, 3)) for ref in projects},
        owner_allows=set(rng.sample(peers, 4)),
        owner_denies=set(rng.sample(peers, 1)),
        owner_mode=rng.choice(("allowlist", "all_cloud_chats")),
        owner_scope=OwnerScope(*(rng.random() > 0.3 for _ in range(4))),
    ), peers


def test_evaluate_and_evaluate_with_trace_agree_everywhere():
    rng = random.Random(20260924)
    for _ in range(2000):
        view, peers = _view(rng)
        request = AuthorityRequest(
            rng.choice(("read", "cross_search", "discover")),
            rng.choice(("c", "missing")),
            tuple(rng.sample(["p0", "p1", "p2", "px"], rng.randint(0, 2))),
            peer_identity=rng.choice([None, *peers]),
        )
        assert evaluate(view, request) == evaluate_with_trace(view, request)[0]


def test_a_success_trace_lists_every_layer_in_order():
    view = make_view(
        clients={"c": ClientState("c", True, "prn")},
        projects={"p": ProjectState("p", True, 1)},
        grants={("c", "p"): ClientProjectGrant(True, False, "full_text", None, "g")},
        memberships={"p": {"user:1"}},
        owner_allows={"user:1"},
        owner_scope=OwnerScope(False, True, True, True),
    )
    request = AuthorityRequest("read", "c", ("p",), "user:1", facts=PeerFacts("private", False))
    verdict, trace = evaluate_with_trace(view, request)
    assert not isinstance(verdict, Denial)
    assert [s for s, _ in trace] == [
        "client",
        "project",
        "client_enabled",
        "grant",
        "owner_deny",
        "owner_allow",
        "membership",
        "owner_class",
        "egress",
    ]
    assert trace[-1] == ("egress", "full_text")


def test_the_class_step_denies_only_on_facts():
    base = dict(
        clients={"c": ClientState("c", True, "prn")},
        projects={"p": ProjectState("p", True, 1)},
        grants={("c", "p"): ClientProjectGrant(True, False, "full_text", None, "g")},
        memberships={"p": {"user:1"}},
        owner_allows={"user:1"},
        owner_scope=OwnerScope(False, False, True, True),  # private excluded
    )
    view = make_view(**base)
    denied, trace = evaluate_with_trace(
        view, AuthorityRequest("read", "c", ("p",), "user:1", facts=PeerFacts("private", False))
    )
    assert isinstance(denied, Denial) and trace[-1] == ("owner_class", "deny:NOT_ACCESSIBLE")
    allowed, trace = evaluate_with_trace(view, AuthorityRequest("read", "c", ("p",), "user:1"))
    assert not isinstance(allowed, Denial) and ("owner_class", "facts_unknown") in trace


@pytest.mark.parametrize(
    ("scope", "facts", "expected"),
    [
        (OwnerScope(True, True, True, True), PeerFacts("private", None), True),
        (OwnerScope(False, True, True, True), PeerFacts("private", None), None),
        (OwnerScope(False, False, True, True), PeerFacts("private", None), False),
        (OwnerScope(False, True, True, True), PeerFacts(None, True), False),
        (OwnerScope(True, True, True, True), PeerFacts(None, None), None),
        (OwnerScope(False, True, False, True), PeerFacts("group", False), False),
        # stored channel: broadcast or supergroup (review #8)
        (OwnerScope(True, True, False, False), PeerFacts.from_stored("channel"), False),
        (OwnerScope(True, True, True, False), PeerFacts.from_stored("channel"), None),
        (OwnerScope(True, True, True, True), PeerFacts(None, False, ("supergroup", "channel")), True),
    ],
)
def test_owner_scope_decide(scope, facts, expected):
    assert scope.decide(facts) is expected


def test_stored_facts_are_exact_only_where_the_type_maps_exactly():
    assert PeerFacts.from_stored("user") == PeerFacts("private", None)
    assert PeerFacts.from_stored("chat") == PeerFacts("group", None)
    assert PeerFacts.from_stored("channel") == PeerFacts(None, None, ("supergroup", "channel"))


def test_seams_reexports_the_one_owner_scope():
    from telegram_mcp.authority import policy
    from telegram_mcp.disclosure import seams

    assert seams.OwnerScope is policy.OwnerScope
```

Append this to `tests/security/test_phase5a_architecture.py`:

```python
DECISION_FIELDS = {
    "owner_mode",
    "include_archived",
    "include_private",
    "include_groups",
    "include_channels",
}


def test_owner_policy_is_decided_only_in_authority_policy():
    """0B G13: loads are keyword arguments; any attribute access is a decision."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "authority" / "policy.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in DECISION_FIELDS:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_policy_trace.py tests/security/test_phase5a_architecture.py -q`
Expected: FAIL. `PeerFacts`, `OwnerScope` and `evaluate_with_trace` cannot be imported from `policy`, and the guard names `disclosure/seams.py:160-166`.

- [ ] **Step 3: Implement in `authority/policy.py`**

Add `"OwnerScope"`, `"PeerFacts"`, `"TraceStep"` and `"evaluate_with_trace"` to `__all__`. Add the following after `EffectiveEgress`:

```python
@dataclass(frozen=True)
class PeerFacts:
    """What is known about a chat's §10.4 class. ``None`` means unknown (0B G14).

    ``candidates`` narrows an unknown ``chat_type``: a stored MTProto
    ``channel`` is either a broadcast channel or a supergroup, and if the
    owner excludes both, the answer is a definite no, not "unknown".
    """

    chat_type: str | None  # "private" | "group" | "supergroup" | "channel"
    archived: bool | None
    candidates: tuple[str, ...] = ()

    @classmethod
    def from_stored(cls, peer_type: str) -> PeerFacts:
        """``peers`` stores only the MTProto peer type; a channel may be a supergroup."""
        if peer_type == "channel":
            return cls(None, None, ("supergroup", "channel"))
        return cls({"user": "private", "chat": "group"}.get(peer_type), None)


@dataclass(frozen=True)
class OwnerScope:
    """The owner's §10.4 chat-kind switches; a chat must pass all of them."""

    include_archived: bool
    include_private: bool
    include_groups: bool
    include_channels: bool

    def admits(self, chat_type: str, is_archived: bool) -> bool:
        if is_archived and not self.include_archived:
            return False
        if chat_type == "private":
            return self.include_private
        if chat_type in ("group", "supergroup"):
            return self.include_groups
        return self.include_channels

    def decide(self, facts: PeerFacts) -> bool | None:
        """``admits`` when the facts settle it; ``None`` when they cannot."""
        if facts.chat_type is None:
            if facts.archived and not self.include_archived:
                return False
            if facts.candidates and not any(self.admits(c, False) for c in facts.candidates):
                return False  # excluded whichever subtype it turns out to be
            if facts.candidates and facts.archived is not None:
                verdicts = {self.admits(c, facts.archived) for c in facts.candidates}
                if len(verdicts) == 1:
                    return verdicts.pop()
            return None
        if facts.archived is None:
            if not self.admits(facts.chat_type, False):
                return False  # excluded by class whatever the archive state
            return True if self.include_archived else None
        return self.admits(facts.chat_type, facts.archived)
```

Add `facts: PeerFacts | None = None` as the last field of `AuthorityRequest`. Add `owner_scope: OwnerScope | None = None` as the last field of `AuthorityView`. Add the `owner_scope: OwnerScope | None = None` parameter to `make_view` and pass it through.

Replace `evaluate` with one private decision function and two entry points. The denial codes and reason strings are unchanged:

```python
TraceStep = tuple[str, str]


def _decide(
    view: AuthorityView, request: AuthorityRequest, trace: list[TraceStep] | None
) -> AuthoritySnapshot | Denial:
    def note(step: str, outcome: str = "pass") -> None:
        if trace is not None:
            trace.append((step, outcome))

    def deny(step: str, code: str, reason: str) -> Denial:
        note(step, f"deny:{code}")
        return Denial(code, reason)

    client = view.clients.get(request.client_ref)
    if client is None:
        return deny("client", REF_NOT_FOUND, "unknown client")
    note("client")
    for ref in request.project_refs:
        if ref not in view.projects:
            return deny("project", REF_NOT_FOUND, "unknown project")
    for ref in request.project_refs:
        if not view.projects[ref].enabled:
            return deny("project", NOT_ACCESSIBLE, "project disabled")
    note("project")
    if not client.enabled:
        return deny("client_enabled", CLIENT_REVOKED, "client disabled")
    note("client_enabled")

    contributing: list[ClientProjectGrant] = []
    for ref in request.project_refs:
        grant = view.grants.get((request.client_ref, ref))
        if grant is None:
            return deny("grant", NOT_ACCESSIBLE, "no grant row")
        if request.operation in ("read", "cross_search") and not grant.can_read:
            return deny("grant", NOT_ACCESSIBLE, "grant denies read")
        contributing.append(grant)
    note("grant")
    if request.operation == "cross_search":
        for ref in request.project_refs:
            if not view.grants[(request.client_ref, ref)].can_cross_search:
                return deny("cross_search", NOT_ACCESSIBLE, "grant denies cross-project search")
        note("cross_search")

    peer = request.peer_identity
    if peer is None:
        if request.operation in ("read", "cross_search"):
            return deny("peer", NOT_ACCESSIBLE, "peer identity required")
        note("peer", "skip")
    else:
        if peer in view.owner_denies:
            return deny("owner_deny", NOT_ACCESSIBLE, "owner denies peer")
        note("owner_deny")
        # §10.4: under allowlist only listed peers are readable -- an empty
        # list admits nothing. all_cloud_chats admits members unless denied.
        if view.owner_mode == "allowlist" and peer not in view.owner_allows:
            return deny("owner_allow", NOT_ACCESSIBLE, "owner does not allow peer")
        note("owner_allow")
        for ref in request.project_refs:
            if peer not in view.memberships.get(ref, frozenset()):
                return deny("membership", NOT_ACCESSIBLE, "peer not a project member")
        note("membership")
        if view.owner_scope is None or request.facts is None:
            note("owner_class", "facts_unknown")
        else:
            admitted = view.owner_scope.decide(request.facts)
            if admitted is False:
                return deny("owner_class", NOT_ACCESSIBLE, "owner scope excludes chat class")
            note("owner_class", "pass" if admitted else "facts_unknown")

    egress = _effective_egress(contributing)
    note("egress", egress.level)
    return AuthoritySnapshot(
        policy_epoch=view.policy_epoch,
        security_epoch=view.security_epoch,
        project_epochs={ref: view.projects[ref].project_epoch for ref in request.project_refs},
        grant_digests={
            ref: view.grants[(request.client_ref, ref)].grant_digest for ref in request.project_refs
        },
        peer_identity=peer,
        effective_egress=egress,
    )


def evaluate(view: AuthorityView, request: AuthorityRequest) -> AuthoritySnapshot | Denial:
    """Intersect all authority layers; allow only if every layer passes."""
    return _decide(view, request, None)


def evaluate_with_trace(
    view: AuthorityView, request: AuthorityRequest
) -> tuple[AuthoritySnapshot | Denial, tuple[TraceStep, ...]]:
    """The same decision, with the ordered steps that produced it (spec §33.1)."""
    trace: list[TraceStep] = []
    verdict = _decide(view, request, trace)
    return verdict, tuple(trace)
```

The original loops checked existence for every project before enablement for any, and so does `_decide`. The two `deny("project", …)` sites keep that order. The random equivalence test proves the verdicts and messages are identical.

- [ ] **Step 4: Move the class out of `seams.py`, and load the scope**

In `disclosure/seams.py`, delete the `class OwnerScope` block (lines 150-166). Change the policy import to `from telegram_mcp.authority.policy import AuthorityRequest, Denial, OwnerScope, evaluate, readable_members`. Leave `"OwnerScope"` in `__all__`: the name stays importable from `seams` but is defined once.

In `storage/authority_view.py`, import `OwnerScope` from `authority.policy` and change the end of `load_view` to:

```python
    return make_view(
        clients=clients,
        projects=projects,
        grants=grants,
        memberships={ref: peers for ref, peers in memberships.items()},
        owner_allows=allows,
        owner_denies=denies,
        policy_epoch=int(policy[0]) if policy else 0,
        security_epoch=security_epoch,
        owner_mode=policy[1] if policy else "allowlist",
        owner_scope=OwnerScope(
            *load_owner_scope(conn, principal_id=principal_id, account_id=account_id)
        ),
    )
```

`load_owner_scope` is defined below `load_view` in the same module; Python resolves it at call time.

After this task, `evaluate` without `facts` behaves exactly as before, as the random equivalence test proves. The tool paths still make their own class decision with `OwnerScope.admits` at four sites. **Task 4A removes that second decision path.** Task 4 alone does not satisfy design §2.2.

- [ ] **Step 5: Run the targeted tests and the suite**

Run: `uv run pytest tests/unit/test_policy_trace.py tests/security/test_phase5a_architecture.py -q && uv run pytest -q`
Expected: 14 targeted trace tests (9 `decide` cases) and 4 architecture tests pass; full suite `+15` over Task 3's count (14 trace tests and 1 guard).

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/authority/policy.py src/telegram_mcp/disclosure/seams.py \
  src/telegram_mcp/storage/authority_view.py tests/unit/test_policy_trace.py \
  tests/security/test_phase5a_architecture.py
git commit -m "feat: one policy evaluator with traces; owner class rule lives in authority"
```

---

### Task 4A: Retrieval decides chat class through the evaluator (review #1)

Design §2.2 says retrieval passes live dialog facts **into** the one evaluator. Today four retrieval sites make their own decision with `snapshot.owner_scope.admits(...)`: `telegram/reads.py:123`, `:231`, `:386` and `:676`. That is a second decision path. This task removes it.

**Files:**
- Modify: `src/telegram_mcp/authority/policy.py` (`admit_live`)
- Modify: `src/telegram_mcp/disclosure/seams.py:187,237-239,500-506` (`ProjectSnapshot.view` replaces `owner_scope`)
- Modify: `src/telegram_mcp/disclosure/search_authority.py:76,141,151,264` (`SearchSnapshot.view` replaces `owner_scope`)
- Modify: `src/telegram_mcp/telegram/reads.py:123,231,386,676`
- Test: `tests/unit/test_policy_trace.py`, `tests/integration/test_telegram_reads.py`, `tests/integration/test_search_reads.py`, `tests/security/test_phase5a_architecture.py`

**Interfaces:**
- Consumes (Task 4): `PeerFacts`, `evaluate_with_trace`, and `AuthorityView.owner_scope`, which `load_view` now fills.
- Produces:
  - `policy.admit_live(view, request, *, chat_type: str, archived: bool) -> bool`
  - `ProjectSnapshot.view: AuthorityView` and `ProjectSnapshot.live_request(identity) -> AuthorityRequest`
  - `SearchSnapshot.view: AuthorityView` and `SearchSnapshot.live_request(identity) -> AuthorityRequest | None`
  - The `owner_scope` field is removed from both snapshots.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_policy_trace.py`:

```python
def test_admit_live_is_the_evaluator_with_live_facts():
    from telegram_mcp.authority.policy import admit_live

    view = make_view(
        clients={"c": ClientState("c", True, "prn")},
        projects={"p": ProjectState("p", True, 1)},
        grants={("c", "p"): ClientProjectGrant(True, False, "full_text", None, "g")},
        memberships={"p": {"user:1", "channel:2"}},
        owner_allows={"user:1", "channel:2"},
        owner_scope=OwnerScope(False, True, True, False),  # archived and channels excluded
    )
    read = lambda identity: AuthorityRequest("read", "c", ("p",), identity)  # noqa: E731
    assert admit_live(view, read("user:1"), chat_type="private", archived=False) is True
    assert admit_live(view, read("user:1"), chat_type="private", archived=True) is False
    assert admit_live(view, read("channel:2"), chat_type="channel", archived=False) is False
    assert admit_live(view, read("channel:2"), chat_type="supergroup", archived=False) is True
    _verdict, trace = evaluate_with_trace(
        view,
        AuthorityRequest("read", "c", ("p",), "user:1", facts=PeerFacts("private", True)),
    )
    assert trace[-1] == ("owner_class", "deny:NOT_ACCESSIBLE")
```

Append to `tests/security/test_phase5a_architecture.py`:

```python
def test_only_authority_policy_calls_the_class_rule():
    """Review #1: no retrieval code decides chat class on its own."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "authority" / "policy.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"admits", "decide"}
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_snapshots_carry_the_view_not_a_private_owner_scope():
    from dataclasses import fields

    from telegram_mcp.disclosure.search_authority import SearchSnapshot
    from telegram_mcp.disclosure.seams import ProjectSnapshot

    for cls in (ProjectSnapshot, SearchSnapshot):
        names = {f.name for f in fields(cls)}
        assert "view" in names and "owner_scope" not in names, cls
```

In `tests/integration/test_telegram_reads.py`, next to the existing `test_a_chat_the_owner_scope_hides_is_not_readable` (`:386`), add one test per class. Reuse that test's `world` fixture and its way of flipping a `policy_state` switch. Read it first (`sed -n 380,420p tests/integration/test_telegram_reads.py`), then write each new test in the same shape:

- **`test_an_archived_member_is_not_readable_when_archived_chats_are_excluded`**: mark the member dialog archived in the fake client (`folder_id=1`, per `telethon_adapter.py:807`) and set `include_archived = 0`. The read must be refused with `NOT_ACCESSIBLE`.
- **`test_a_private_member_is_not_readable_when_private_chats_are_excluded`**: set `include_private = 0`. Expect `NOT_ACCESSIBLE`.
- **`test_a_group_member_is_not_readable_when_groups_are_excluded`**: set `include_groups = 0`. Expect `NOT_ACCESSIBLE`.
- **`test_a_channel_member_is_not_readable_when_channels_are_excluded`**: set `include_channels = 0`. Expect `NOT_ACCESSIBLE`.

Each test also asserts that the decision came from the evaluator. Monkeypatch `telegram_mcp.authority.policy.admit_live` with a recording wrapper that calls the real function, and assert that it recorded a `False` for the tested identity. Because `admit_live` is the only function that may decide the class (guard above), a recorded `False` from it proves the evaluator's `owner_class` step denied the read.

In `tests/integration/test_search_reads.py`, add `test_search_skips_a_peer_the_owner_class_excludes`. Using the file's existing search world, set `include_channels = 0` and search. No hit from the channel member may appear, and the recording wrapper must show `admit_live(...) is False` for that peer.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_policy_trace.py tests/security/test_phase5a_architecture.py -q`
Expected: FAIL. `admit_live` does not exist; the guard names `telegram/reads.py:123,231,386,676`; the snapshots still have `owner_scope`.

- [ ] **Step 3: Implement `admit_live` in `authority/policy.py`**

Add it to `__all__`:

```python
def admit_live(
    view: AuthorityView, request: AuthorityRequest, *, chat_type: str, archived: bool
) -> bool:
    """Retrieval's class/archive decision, made by the one evaluator (design §2.2).

    The request is re-evaluated in full with the live dialog facts, so the
    ``owner_class`` step, not the caller, decides.
    """
    live = AuthorityRequest(
        request.operation,
        request.client_ref,
        request.project_refs,
        request.peer_identity,
        facts=PeerFacts(chat_type, archived),
    )
    return not isinstance(evaluate(view, live), Denial)
```

- [ ] **Step 4: Replace `owner_scope` with `view` in both snapshots**

In `disclosure/seams.py`, replace `owner_scope: OwnerScope` in `ProjectSnapshot` with `view: AuthorityView`, importing `AuthorityView` from `authority.policy`. Add this method:

```python
    def live_request(self, identity: str) -> AuthorityRequest:
        return AuthorityRequest("read", self.client_ref, (self.project_ref,), identity)
```

At the construction site (`:500-506`), replace the `owner_scope=OwnerScope(*load_owner_scope(...))` argument with `view=view`. The `view` is the `load_view(...)` result already in scope at that site (`:560`). Confirm the local variable name with `sed -n 555,570p src/telegram_mcp/disclosure/seams.py` before editing. Delete the `owner_scope=lambda ...` argument passed to `SearchAuthority` (`:237-239`), and delete the `load_owner_scope` import if nothing else uses it.

In `disclosure/search_authority.py`:
- Replace `owner_scope: Any  # seams.OwnerScope` with `view: AuthorityView`.
- Delete the constructor's `owner_scope` parameter and `self._owner_scope`.
- At `:264`, pass `view=view` (the `load_view` result at `:187`).
- Add to `SearchSnapshot`:

```python
    def live_request(self, identity: str) -> AuthorityRequest | None:
        refs = tuple(p.project_ref for p in self.projects if identity in p.readable)
        if not refs:
            return None
        operation = "cross_search" if self.tool_name == "telegram_cross_project_search" else "read"
        return AuthorityRequest(operation, self.client_ref, refs, identity)
```

- [ ] **Step 5: Route the four retrieval sites through `admit_live`**

In `telegram/reads.py`, add `from telegram_mcp.authority.policy import admit_live`, then:

- `:123`: `if view is None or not admit_live(snapshot.view, snapshot.live_request(identity), chat_type=view.chat_type, archived=view.is_archived):`
- `:231`: `if dialog is None or not admit_live(snapshot.view, snapshot.live_request(snapshot.peer_identity), chat_type=dialog.chat_type, archived=dialog.is_archived):`. `peer_identity` is set for per-peer tools, and the existing code already relies on it.
- `:386`: the same form as `:231`.
- `:676`:

```python
                request = snapshot.live_request(peer)
                admitted[peer] = request is not None and admit_live(
                    snapshot.view, request, chat_type=view.chat_type, archived=view.is_archived
                )
```

`snapshot.view` is frozen at snapshot time, as `readable` already was, and step-8 revalidation still catches any drift after the snapshot. The re-evaluation also repeats the grant and membership checks, which is intended: the class decision can only narrow what `readable` allowed.

- [ ] **Step 6: Run the targeted tests, then the suite**

Run: `uv run pytest tests/unit/test_policy_trace.py tests/security/test_phase5a_architecture.py tests/integration/test_telegram_reads.py tests/integration/test_search_reads.py -q && uv run pytest -q`
Expected: all pass; full suite `+8` over Task 4's count (1 unit, 2 guards, 4 reads, 1 search). If a Phase-4 test built a snapshot with `owner_scope=` directly, `grep -rn "owner_scope=" tests` finds it. Change it to `view=` with a `make_view(owner_scope=...)` view. That is the intended interface change, and each such test is named in the commit message.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_mcp/authority/policy.py src/telegram_mcp/disclosure/seams.py \
  src/telegram_mcp/disclosure/search_authority.py src/telegram_mcp/telegram/reads.py \
  tests/unit/test_policy_trace.py tests/security/test_phase5a_architecture.py \
  tests/integration/test_telegram_reads.py tests/integration/test_search_reads.py
git commit -m "feat: retrieval decides chat class through the one evaluator with live facts"
```

---

### Task 5: `EffectiveAccess`: snapshot, digest and diff

**Files:**
- Create: `src/telegram_mcp/authority/effective.py` (pure, no storage)
- Create: `src/telegram_mcp/storage/effective_access.py` (SQLite glue)
- Test: `tests/unit/test_effective_access.py`

**Interfaces:**
- Consumes (Task 4): `evaluate_with_trace`, `PeerFacts`, `AuthorityRequest`, `AuthoritySnapshot`, `Denial`.
- Produces:
  - `effective.AccessRow`, a frozen dataclass with `.key -> tuple[str, str, str, str]` and `.to_json() -> dict`
  - `effective.effective_rows(view, peers: Mapping[str, tuple[str, PeerFacts]]) -> tuple[AccessRow, ...]`
  - `effective.rows_digest(rows) -> str`
  - `effective.diff_rows(before, after) -> dict[str, list[dict]]`, with keys `added`, `removed` and `changed`
  - `effective_access.snapshot(conn) -> tuple[AccessRow, ...]`
  - `effective_access.explain(conn, *, client_ref, project_ref, peer_ref=None) -> list[dict]`
  - `effective_access.base_digest(conn) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_effective_access.py
"""Metadata-effective access (design §2.3): refs only, deterministic, diffable."""

import json

import pytest

from telegram_mcp.authority.effective import diff_rows, rows_digest
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.effective_access import base_digest, explain, snapshot
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows, seed_project_world

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    return conn, refs


def test_one_project_row_and_two_rows_per_member(world):
    conn, _refs = world
    rows = snapshot(conn)
    assert len(rows) == 1 + 3 * 2  # discover row + (read, cross_search) x 3 members
    assert [r.key for r in rows] == sorted(r.key for r in rows)


def test_decisions_follow_the_one_evaluator(world):
    conn, refs = world
    by_key = {r.key: r for r in snapshot(conn)}
    ali = by_key[(CLIENT, PROJECT_REF, refs["user:100"], "read")]
    assert (ali.decision, ali.egress, ali.facts) == ("allow", "full_text", "unknown")
    assert ali.owner_class == "unknown"  # archive state is never stored: conditional allow
    team = by_key[(CLIENT, PROJECT_REF, refs["chat:9"], "read")]
    assert team.decision == "allow"  # include_groups = 1
    cross = by_key[(CLIENT, PROJECT_REF, refs["user:100"], "cross_search")]
    assert cross.decision == "NOT_ACCESSIBLE"  # the grant has can_cross_search = 0


IDENTITIES = {"user:100", "user:101", "channel:7", "chat:9"}
RAW_IDS = {100, 101, 7, 9, "100", "101", "7", "9"}


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _values(v)
    else:
        yield obj


def test_no_row_carries_a_raw_identity(world):
    conn, _refs = world
    produced = [r.to_json() for r in snapshot(conn)]
    produced += explain(conn, client_ref=CLIENT, project_ref=PROJECT_REF)
    for value in _values(produced):
        assert value not in IDENTITIES and value not in RAW_IDS, value
        if isinstance(value, str):
            assert not any(identity in value for identity in IDENTITIES), value
    assert "user:100" not in json.dumps(produced)


def test_digest_is_stable_and_moves_with_policy(world):
    conn, _refs = world
    first = rows_digest(snapshot(conn))
    assert rows_digest(snapshot(conn)) == first
    base = base_digest(conn)
    conn.execute("UPDATE client_projects SET egress_level = 'metadata_only'")
    conn.commit()
    assert rows_digest(snapshot(conn)) != first
    assert base_digest(conn) != base


def test_diff_reports_changed_rows_only(world):
    conn, refs = world
    before = snapshot(conn)
    conn.execute("UPDATE client_projects SET egress_level = 'metadata_only'")
    conn.commit()
    diff = diff_rows(before, snapshot(conn))
    assert diff["added"] == [] and diff["removed"] == []
    changed = {tuple(c["key"]) for c in diff["changed"]}
    assert (CLIENT, PROJECT_REF, refs["user:100"], "read") in changed
    assert all(c["before"]["egress"] == "full_text" for c in diff["changed"])


def test_an_archive_switch_is_a_visible_change(world):
    conn, refs = world
    before = snapshot(conn)
    conn.execute("UPDATE policy_state SET include_archived = 1")
    conn.commit()
    changed = {tuple(c["key"]): c for c in diff_rows(before, snapshot(conn))["changed"]}
    ali = changed[(CLIENT, PROJECT_REF, refs["user:100"], "read")]
    assert ali["before"]["owner_class"] == "unknown" and ali["after"]["owner_class"] == "pass"


def test_a_channel_excluded_either_way_is_denied_not_unknown(world):
    conn, refs = world
    conn.execute("UPDATE policy_state SET include_groups = 0, include_channels = 0")
    conn.commit()
    news = {r.key: r for r in snapshot(conn)}[(CLIENT, PROJECT_REF, refs["channel:7"], "read")]
    assert (news.decision, news.owner_class) == ("NOT_ACCESSIBLE", "deny")


def test_explain_carries_the_ordered_trace(world):
    conn, refs = world
    rows = explain(conn, client_ref=CLIENT, project_ref=PROJECT_REF, peer_ref=refs["user:100"])
    read = next(r for r in rows if r["operation"] == "read")
    assert [s for s, _ in read["trace"]][:3] == ["client", "project", "client_enabled"]
    assert read["trace"][-1] == ["egress", "full_text"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_effective_access.py -q`
Expected: FAIL with `ModuleNotFoundError: telegram_mcp.authority.effective`.

- [ ] **Step 3: Implement `authority/effective.py`**

```python
"""Metadata-effective access (Phase-5 design §2.3). Pure: no storage, no Telegram.

A row is what the one evaluator decides for one client, project and member
under the stored metadata. Rows carry opaque refs only; canonical
``type:id`` identities stay inside this function.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from telegram_mcp.authority.policy import (
    AuthorityRequest,
    AuthorityView,
    Denial,
    PeerFacts,
    TraceStep,
    evaluate_with_trace,
)
from telegram_mcp.consent.challenge import jcs_dumps

__all__ = ["AccessRow", "diff_rows", "effective_rows", "rows_digest", "trace_digest"]


def trace_digest(trace: Iterable[TraceStep]) -> str:
    return hashlib.sha256(jcs_dumps([list(step) for step in trace])).hexdigest()


@dataclass(frozen=True)
class AccessRow:
    client_ref: str
    project_ref: str
    peer_ref: str  # "" for the project-level row
    operation: str  # "discover" | "read" | "cross_search"
    decision: str  # "allow" or a denial code
    egress: str  # "" when denied
    excerpt_max: int | None
    facts: str  # "n/a" | "exact" | "unknown"
    owner_class: str  # "n/a" | "pass" | "unknown" | "deny": allow with "unknown" is conditional
    trace_digest: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.client_ref, self.project_ref, self.peer_ref, self.operation)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _row(
    view: AuthorityView,
    request: AuthorityRequest,
    *,
    peer_ref: str,
    facts: str,
) -> AccessRow:
    verdict, trace = evaluate_with_trace(view, request)
    outcome = dict(trace).get("owner_class")
    owner_class = {
        None: "n/a",
        "pass": "pass",
        "facts_unknown": "unknown",
    }.get(outcome, "deny")
    if isinstance(verdict, Denial):
        decision, egress, excerpt = verdict.code, "", None
    else:
        decision = "allow"
        egress = verdict.effective_egress.level
        excerpt = verdict.effective_egress.excerpt_limit
    return AccessRow(
        client_ref=request.client_ref,
        project_ref=request.project_refs[0],
        peer_ref=peer_ref,
        operation=request.operation,
        decision=decision,
        egress=egress,
        excerpt_max=excerpt,
        facts=facts,
        owner_class=owner_class,
        trace_digest=trace_digest(trace),
    )


def effective_rows(
    view: AuthorityView, peers: Mapping[str, tuple[str, PeerFacts]]
) -> tuple[AccessRow, ...]:
    """Every client x project, plus every member for read and cross_search."""
    rows: list[AccessRow] = []
    for client_ref in sorted(view.clients):
        for project_ref in sorted(view.projects):
            rows.append(
                _row(
                    view,
                    AuthorityRequest("discover", client_ref, (project_ref,)),
                    peer_ref="",
                    facts="n/a",
                )
            )
            for identity in sorted(view.memberships.get(project_ref, frozenset())):
                peer_ref, facts = peers[identity]
                exact = facts.chat_type is not None and facts.archived is not None
                for operation in ("read", "cross_search"):
                    rows.append(
                        _row(
                            view,
                            AuthorityRequest(
                                operation, client_ref, (project_ref,), identity, facts=facts
                            ),
                            peer_ref=peer_ref,
                            facts="exact" if exact else "unknown",
                        )
                    )
    return tuple(sorted(rows, key=lambda r: r.key))


def rows_digest(rows: Iterable[AccessRow]) -> str:
    return hashlib.sha256(jcs_dumps([r.to_json() for r in rows])).hexdigest()


# owner_class is compared, so a class/archive switch that turns "unknown" into
# "pass" is a visible change even when decision stays "allow" (review #8).
_COMPARED = ("decision", "egress", "excerpt_max", "facts", "owner_class")


def diff_rows(
    before: Iterable[AccessRow], after: Iterable[AccessRow]
) -> dict[str, list[dict[str, Any]]]:
    old = {r.key: r for r in before}
    new = {r.key: r for r in after}
    changed = []
    for key in sorted(old.keys() & new.keys()):
        a = {f: getattr(old[key], f) for f in _COMPARED}
        b = {f: getattr(new[key], f) for f in _COMPARED}
        if a != b:
            changed.append({"key": list(key), "before": a, "after": b})
    return {
        "added": [new[k].to_json() for k in sorted(new.keys() - old.keys())],
        "removed": [old[k].to_json() for k in sorted(old.keys() - new.keys())],
        "changed": changed,
    }
```

Stored facts never know the archive state (G14), so `facts` is `"unknown"` for every stored row today. The field exists so live facts (5b drift) can report `"exact"` without a format change.

**An `allow` row whose `owner_class` is `"unknown"` is *conditional* access.** It is allowed if the live chat turns out to pass the class and archive switches, and the runtime decides that through `admit_live` (Task 4A). `policy explain` and `simulate` output shows `owner_class` next to `decision`, so the operator never reads such a row as unconditional.

- [ ] **Step 4: Implement `storage/effective_access.py`**

```python
"""SQLite glue for metadata-effective access (Phase-5 design §2.3)."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from telegram_mcp.authority.effective import AccessRow, effective_rows, rows_digest
from telegram_mcp.authority.policy import (
    AuthorityRequest,
    AuthorityView,
    PeerFacts,
    evaluate_with_trace,
)
from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.storage.authority_view import load_security, load_view, owner_account

__all__ = ["base_digest", "explain", "snapshot"]


def _inputs(
    conn: sqlite3.Connection,
) -> tuple[AuthorityView, dict[str, tuple[str, PeerFacts]]] | None:
    principal = conn.execute("SELECT id FROM principals ORDER BY id LIMIT 1").fetchone()
    if principal is None:
        return None
    account = owner_account(conn, principal_id=int(principal[0]))
    if account is None:
        return None
    view = load_view(conn, principal_id=int(principal[0]), account_id=account)
    peers = {
        f"{row[0]}:{int(row[1])}": (row[2], PeerFacts.from_stored(row[0]))
        for row in conn.execute(
            "SELECT telegram_peer_type, telegram_peer_id, peer_ref FROM peers WHERE account_id = ?",
            (account,),
        )
    }
    return view, peers


def snapshot(conn: sqlite3.Connection) -> tuple[AccessRow, ...]:
    loaded = _inputs(conn)
    if loaded is None:
        return ()
    return effective_rows(*loaded)


def explain(
    conn: sqlite3.Connection, *, client_ref: str, project_ref: str, peer_ref: str | None = None
) -> list[dict[str, Any]]:
    loaded = _inputs(conn)
    if loaded is None:
        return []
    view, peers = loaded
    if project_ref not in view.projects:
        raise ValueError("unknown project_ref")
    by_ref = {ref: (identity, facts) for identity, (ref, facts) in peers.items()}
    if peer_ref is not None and peer_ref not in by_ref:
        raise ValueError("unknown peer_ref")
    requests: list[tuple[str, AuthorityRequest]] = []
    if peer_ref is None:
        requests.append(("", AuthorityRequest("discover", client_ref, (project_ref,))))
    members = view.memberships.get(project_ref, frozenset())
    targets = (
        [peer_ref]
        if peer_ref is not None
        else sorted(ref for ref, (identity, _facts) in by_ref.items() if identity in members)
    )
    for ref in targets:
        identity, facts = by_ref[ref]
        for operation in ("read", "cross_search"):
            requests.append(
                (ref, AuthorityRequest(operation, client_ref, (project_ref,), identity, facts=facts))
            )
    out = []
    for ref, request in requests:
        verdict, trace = evaluate_with_trace(view, request)
        out.append(
            {
                "client_ref": client_ref,
                "project_ref": project_ref,
                "peer_ref": ref,
                "operation": request.operation,
                "decision": getattr(verdict, "code", "allow"),
                "trace": [list(step) for step in trace],
            }
        )
    return out


def base_digest(conn: sqlite3.Connection) -> str:
    """What a staged change was computed against (design §2.3 ``tps_`` binding)."""
    security_epoch, locked = load_security(conn)
    policy = [
        [int(r[0]), int(r[1]), int(r[2])]
        for r in conn.execute(
            "SELECT principal_id, account_id, policy_epoch FROM policy_state ORDER BY 1, 2"
        )
    ]
    projects = {
        r[0]: int(r[1])
        for r in conn.execute("SELECT project_ref, project_epoch FROM projects ORDER BY 1")
    }
    body = {
        "locked": bool(locked),
        "policy": policy,
        "projects": projects,
        "security_epoch": security_epoch,
        "snapshot": rows_digest(snapshot(conn)),
    }
    return hashlib.sha256(jcs_dumps(body)).hexdigest()
```

`jcs_dumps` refuses floats and non-ASCII keys. Every key here is ASCII and every number an `int`.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/unit/test_effective_access.py -q && uv run pytest -q`
Expected: 8 passed; full suite `+8`.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/authority/effective.py src/telegram_mcp/storage/effective_access.py \
  tests/unit/test_effective_access.py
git commit -m "feat: metadata-effective access snapshots, digests and diffs"
```

---

### Task 6: The staging registry and `policy explain` / `simulate` / `diff`

Revision 3 fixes three things in this task:

- **Review #4:** stages are bound to the admin peer, the owner principal, the active account and the security epoch, and the registry is capped.
- **Review #3:** simulate-equals-commit is a **semantic** diff that normalises refs minted inside the transaction, and it is tested over **every** simulatable command.
- **Review #2:** a test proves simulation leaves a `tgl_` handle usable for the real command.

**Files:**
- Modify: `src/telegram_mcp/ipc/admin.py` (the `ADMIN_PEER` context variable, set by `serve_admin`)
- Modify: `src/telegram_mcp/authority/effective.py` (`normalize_new_refs`)
- Modify: `src/telegram_mcp/storage/effective_access.py` (`known_refs`, `current_binding`)
- Create: `src/telegram_mcp/authority/staging.py`
- Create: `src/telegram_mcp/ipc/handlers/policy.py`
- Test: `tests/unit/test_policy_handlers.py`

**Interfaces:**
- Consumes: `simulate_tx`, `run_tx` (Task 2); `PROJECT_COMMANDS`, `scope_commands` (Task 3); `member_commands`, `CLIENT_COMMANDS` (Task 9, which is written against this interface); `snapshot`, `explain`, `base_digest`, `diff_rows` (Task 5).
- Produces:
  - `admin.ADMIN_PEER: ContextVar[PeerCredentials | None]`
  - `staging.Binding(peer_uid: int | None, principal_ref: str, account_ref: str, security_epoch: int)`
  - `StagingRegistry(clock=time.monotonic, max_live=64)`, with `.stage(*, binding, base_digest, payload, diff) -> str`, `.get(handle, *, binding, current_base) -> Staged` and `__len__`
  - `effective.normalize_new_refs(diff, known: set[str]) -> dict`
  - `effective_access.known_refs(conn) -> set[str]`
  - `effective_access.current_binding(conn, peer_uid: int | None) -> Binding`
  - `policy_handlers(conn, *, registry, simulatable: Mapping[str, TxCommand]) -> dict[str, Handler]`

Because Task 9 adds `member_commands` and `client disable`, **run Task 9 before Task 6**, or stub `member_commands` as `{}` until Task 9 lands. The all-commands test below lists `project remove-peer` and `client disable`, so it can only pass once Task 9 exists. Execution order for this plan: 1, 2, 3, 4, 4A, 5, 9, 6, 7, 8, 10, 11.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_policy_handlers.py
"""policy explain / simulate / diff (design §2.3; review #2, #3, #4; Review Focus #5)."""

from types import SimpleNamespace

import pytest

from telegram_mcp.authority.effective import diff_rows, normalize_new_refs
from telegram_mcp.authority.staging import StagingRegistry
from telegram_mcp.ipc.admin import ADMIN_PEER, PeerCredentials
from telegram_mcp.ipc.handlers._wrapper import run_tx
from telegram_mcp.ipc.handlers.clients import CLIENT_COMMANDS
from telegram_mcp.ipc.handlers.policy import policy_handlers
from telegram_mcp.ipc.handlers.projects import PROJECT_COMMANDS, member_commands, project_handlers
from telegram_mcp.ipc.handlers.scope import scope_commands
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.effective_access import known_refs, snapshot
from telegram_mcp.telegram.discovery import DiscoveryStore
from tests.authority_fixtures import (
    BETA_REF,
    PROJECT_REF,
    seed_authority_rows,
    seed_project_world,
    seed_second_project,
)

CLIENT = "tcl_" + "a" * 26


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def _view(peer_type, peer_id, name):
    chat = {"user": "private", "chat": "group", "channel": "channel"}[peer_type]
    return SimpleNamespace(
        peer_type=peer_type, peer_id=peer_id, display_name=name, username=None, chat_type=chat
    )


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    seed_project_world(conn)
    seed_second_project(conn)
    discovery, members = DiscoveryStore(), DiscoveryStore()
    commands = {
        **PROJECT_COMMANDS,
        **scope_commands(discovery),
        **member_commands(members),
        **CLIENT_COMMANDS,
    }
    clock = Clock()
    registry = StagingRegistry(clock=clock)
    handlers = policy_handlers(conn, registry=registry, simulatable=commands)
    return SimpleNamespace(
        conn=conn,
        h=handlers,
        registry=registry,
        clock=clock,
        commands=commands,
        discovery=discovery,
        projects=project_handlers(conn, members=members),
    )


def _logical_state(conn):
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        )
    ]
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}


def _policy_epoch(conn):
    return conn.execute("SELECT policy_epoch FROM policy_state").fetchone()[0]


def _handle(w, peer_type, peer_id, name):
    listed = w.discovery.new_snapshot([_view(peer_type, peer_id, name)], _policy_epoch(w.conn))
    return listed[0]["handle"]


def _member_handle(w, project_ref, peer_ref):
    listed = w.projects["project members"]({"project_ref": project_ref})["members"]
    return next(m["handle"] for m in listed if m["peer_ref"] == peer_ref)


def _cases(w):
    """Every simulatable policy/project/client mutation (spec §33.1)."""
    bob = w.conn.execute(
        "SELECT peer_ref FROM peers WHERE telegram_peer_id = 101"
    ).fetchone()[0]
    return [
        ("project create", lambda: {"slug": "gamma", "display_name": "Gamma"}),
        ("project rename", lambda: {"project_ref": BETA_REF, "display_name": "Beta 2"}),
        ("project enable", lambda: {"project_ref": BETA_REF}),
        ("project disable", lambda: {"project_ref": BETA_REF}),
        (
            "project grant-client",
            lambda: {"project_ref": BETA_REF, "client_ref": CLIENT, "egress_level": "full_text"},
        ),
        (
            "project set-egress",
            lambda: {
                "project_ref": PROJECT_REF,
                "client_ref": CLIENT,
                "egress_level": "excerpt",
                "excerpt_max_codepoints": 100,
            },
        ),
        ("project revoke-client", lambda: {"project_ref": PROJECT_REF, "client_ref": CLIENT}),
        ("project revoke-cross-search", lambda: {"project_ref": BETA_REF, "client_ref": CLIENT}),
        ("project grant-cross-search", lambda: {"project_ref": BETA_REF, "client_ref": CLIENT}),
        (
            "project add-peer",
            lambda: {"project_ref": BETA_REF, "handle": _handle(w, "user", 200, "New")},
        ),
        (
            "project remove-peer",
            lambda: {"project_ref": BETA_REF, "handle": _member_handle(w, BETA_REF, bob)},
        ),
        ("scope mode", lambda: {"mode": "all_cloud_chats"}),
        ("scope allow", lambda: {"handle": _handle(w, "user", 300, "Allowed")}),
        ("scope deny", lambda: {"handle": _handle(w, "user", 100, "Ali")}),
        ("scope remove", lambda: {"handle": _handle(w, "user", 101, "Bob")}),
        ("client disable", lambda: {"client": "codex_local"}),
    ]


def test_the_case_list_is_every_simulatable_command(world):
    assert {name for name, _ in _cases(world)} == set(world.commands)


@pytest.mark.parametrize("index", range(16))
def test_simulate_equals_commit_semantically(world, index):
    """Review #3: every command, including ones that mint refs inside the TX."""
    name, make_args = _cases(world)[index]
    args = make_args()
    known = known_refs(world.conn)
    simulated = world.h["policy simulate"]({"command": name, "args": args})["diff"]
    before = snapshot(world.conn)
    run_tx(world.conn, world.commands[name], args)  # the SAME args, handle included
    real = normalize_new_refs(diff_rows(before, snapshot(world.conn)), known)
    assert simulated == real, name


def test_simulation_leaves_a_selection_handle_usable(world):
    """Review #2: take() is non-consuming, so the dry run spends nothing."""
    handle = _handle(world, "user", 300, "Allowed")
    world.h["policy simulate"]({"command": "scope allow", "args": {"handle": handle}})
    assert run_tx(world.conn, world.commands["scope allow"], {"handle": handle}) == {
        "decision": "allow"
    }


def test_simulate_leaves_logical_state_identical(world):
    before = _logical_state(world.conn)
    world.h["policy simulate"]({"command": "project create", "args": {"slug": "g", "display_name": "G"}})
    assert _logical_state(world.conn) == before
    assert len(world.registry) == 1  # the one intended in-memory difference


def test_simulate_refuses_non_policy_commands(world):
    before = _logical_state(world.conn)
    for command in ("lock", "auth login", "policy simulate", "project list", "client rotate"):
        with pytest.raises(ValueError, match="cannot be simulated"):
            world.h["policy simulate"]({"command": command, "args": {}})
    assert _logical_state(world.conn) == before


def test_diff_returns_the_staged_change_until_the_base_moves(world):
    staged = world.h["policy simulate"](
        {"command": "project disable", "args": {"project_ref": BETA_REF}}
    )
    assert world.h["policy diff"]({"staged": staged["staged"]})["diff"] == staged["diff"]
    run_tx(world.conn, PROJECT_COMMANDS["scope mode"], {"mode": "all_cloud_chats"})
    with pytest.raises(ValueError, match="stale"):
        world.h["policy diff"]({"staged": staged["staged"]})


def test_a_stage_is_bound_to_the_admin_peer(world):
    """Review #4: another admin peer cannot read an operator's staged change."""
    token = ADMIN_PEER.set(PeerCredentials(uid=501, gid=20))
    try:
        staged = world.h["policy simulate"](
            {"command": "project disable", "args": {"project_ref": BETA_REF}}
        )
    finally:
        ADMIN_PEER.reset(token)
    token = ADMIN_PEER.set(PeerCredentials(uid=502, gid=20))
    try:
        with pytest.raises(ValueError, match="stale"):
            world.h["policy diff"]({"staged": staged["staged"]})
    finally:
        ADMIN_PEER.reset(token)


def test_the_registry_is_bounded(world):
    registry = StagingRegistry(clock=world.clock, max_live=2)
    handlers = policy_handlers(world.conn, registry=registry, simulatable=world.commands)
    args = {"command": "scope mode", "args": {"mode": "all_cloud_chats"}}
    handlers["policy simulate"](args)
    handlers["policy simulate"](args)
    with pytest.raises(ValueError, match="too many"):
        handlers["policy simulate"](args)


def test_staged_handles_expire(world):
    staged = world.h["policy simulate"]({"command": "scope mode", "args": {"mode": "all_cloud_chats"}})
    world.clock.now = 601.0
    with pytest.raises(ValueError, match="stale"):
        world.h["policy diff"]({"staged": staged["staged"]})


def test_explain_requires_client_and_project(world):
    rows = world.h["policy explain"]({"client_ref": CLIENT, "project_ref": PROJECT_REF})["rows"]
    assert rows[0]["operation"] == "discover"
    with pytest.raises(ValueError):
        world.h["policy explain"]({"client_ref": CLIENT})
```

`_cases` holds 16 commands, and `test_the_case_list_is_every_simulatable_command` fails the moment a new simulatable command is added without a case. That keeps the parametrised range honest.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_policy_handlers.py -q`
Expected: FAIL with `ImportError` (`ADMIN_PEER`, `normalize_new_refs`, `staging`, `policy`).

- [ ] **Step 3: Add `ADMIN_PEER` to `ipc/admin.py`**

```python
from contextvars import ContextVar

# The authenticated admin peer for the request being dispatched (Phase-5
# design §2.3: staged changes are bound to it). Set by serve_admin around
# each request; None for in-process callers.
ADMIN_PEER: ContextVar[PeerCredentials | None] = ContextVar("admin_peer", default=None)
```

Place it after the `PeerCredentials` dataclass and add `"ADMIN_PEER"` to `__all__`. In `serve_admin.handle`, wrap the dispatch:

```python
                peer_token = ADMIN_PEER.set(creds)
                try:
                    await write_frame(writer, encode_json_frame(await router.adispatch(request)))
                finally:
                    ADMIN_PEER.reset(peer_token)
```

This replaces the existing single `await write_frame(...)` line. Keep the approver block above it unchanged.

- [ ] **Step 4: Add `normalize_new_refs` to `authority/effective.py`**

```python
from telegram_mcp.opaque import validate_ref_format


def normalize_new_refs(diff: Mapping[str, Any], known: set[str]) -> dict[str, Any]:
    """Replace refs minted inside the transaction with ``new:<prefix>``, then re-sort.

    A simulated ``project create`` mints one ``tpr_`` and the real commit
    mints another, so equality is semantic. One command mints at most one
    ref per prefix, so ``new:tpr_`` is unambiguous within one diff.
    """

    def fix(value: Any) -> Any:
        if isinstance(value, str) and value not in known:
            try:
                prefix = validate_ref_format(value)
            except ValueError:
                return value
            return "new:" + prefix
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value

    out = {key: fix(list(entries)) for key, entries in diff.items()}
    for key in ("added", "removed"):
        out[key] = sorted(
            out[key],
            key=lambda r: (r["client_ref"], r["project_ref"], r["peer_ref"], r["operation"]),
        )
    out["changed"] = sorted(out["changed"], key=lambda c: c["key"])
    return out
```

Add `"normalize_new_refs"` to `__all__`, and add `Mapping` to the `collections.abc` import if it is not already there. `trace_digest` is a 64-hex string, which fails `validate_ref_format`, so it is never rewritten.

- [ ] **Step 5: Add `known_refs` and `current_binding` to `storage/effective_access.py`**

```python
from telegram_mcp.authority.staging import Binding


def known_refs(conn: sqlite3.Connection) -> set[str]:
    refs: set[str] = set()
    for sql in (
        "SELECT project_ref FROM projects",
        "SELECT peer_ref FROM peers",
        "SELECT client_ref FROM mcp_clients",
    ):
        refs.update(r[0] for r in conn.execute(sql))
    return refs


def current_binding(conn: sqlite3.Connection, peer_uid: int | None) -> Binding:
    principal = conn.execute(
        "SELECT id, principal_ref FROM principals ORDER BY id LIMIT 1"
    ).fetchone()
    account = owner_account(conn, principal_id=int(principal[0])) if principal else None
    if principal is None or account is None:
        raise ValueError("log in first: no account exists")
    account_ref = conn.execute(
        "SELECT account_ref FROM accounts WHERE id = ?", (account,)
    ).fetchone()[0]
    security_epoch, _locked = load_security(conn)
    return Binding(peer_uid, principal[1], account_ref, security_epoch)
```

Add both to `__all__`.

- [ ] **Step 6: Implement `authority/staging.py`**

```python
"""Staged policy changes, ``tps_`` handles (Phase-5 design §2.3).

In daemon memory only, for ten minutes, at most ``max_live`` at once. A
stage is bound to the admin peer, the owner principal, the active account,
the security epoch, and the base digest the change was computed against. A
mismatch on any of them refuses rather than re-diffing silently: the
operator approved a diff, not a moving target.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["Binding", "Staged", "StagingRegistry"]

TTL_S = 600.0
MAX_LIVE = 64


@dataclass(frozen=True)
class Binding:
    peer_uid: int | None
    principal_ref: str
    account_ref: str
    security_epoch: int


@dataclass(frozen=True)
class Staged:
    handle: str
    binding: Binding
    base_digest: str
    payload: Any
    diff: Mapping[str, Any]
    expires_at: float


class StagingRegistry:
    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_live: int = MAX_LIVE
    ) -> None:
        self._clock = clock
        self._max = max_live
        self._items: dict[str, Staged] = {}

    def __len__(self) -> int:
        return len(self._items)

    def _sweep(self) -> None:
        now = self._clock()
        for handle in [h for h, s in self._items.items() if s.expires_at <= now]:
            del self._items[handle]

    def stage(
        self, *, binding: Binding, base_digest: str, payload: Any, diff: Mapping[str, Any]
    ) -> str:
        self._sweep()
        if len(self._items) >= self._max:
            raise ValueError("too many staged changes; let some expire")
        handle = mint_opaque_ref("tps_")
        self._items[handle] = Staged(
            handle, binding, base_digest, payload, diff, self._clock() + TTL_S
        )
        return handle

    def get(self, handle: str, *, binding: Binding, current_base: str) -> Staged:
        self._sweep()
        staged = self._items.get(handle)
        if staged is None or staged.binding != binding or staged.base_digest != current_base:
            raise ValueError("unknown, expired or stale staged change")
        return staged
```

A binding mismatch does **not** delete the stage. Otherwise another peer could destroy an operator's staged change just by guessing at it.

- [ ] **Step 7: Implement `ipc/handlers/policy.py`**

```python
"""policy explain / simulate / diff (spec §33.1; Phase-5 design §2.3).

Read-only for committed state. ``simulate`` runs the real ``plan``/``apply``
of a policy, project or client mutation under a rolled-back savepoint, and
stages the semantic diff, bound to who asked and to what it was computed
against.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from telegram_mcp.authority.effective import diff_rows, normalize_new_refs
from telegram_mcp.authority.staging import StagingRegistry
from telegram_mcp.ipc.admin import ADMIN_PEER
from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, simulate_tx
from telegram_mcp.storage.effective_access import (
    base_digest,
    current_binding,
    explain,
    known_refs,
    snapshot,
)

__all__ = ["policy_handlers"]


def _bare(args: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - allowed:
        raise ValueError("unknown argument")
    return body


def _peer_uid() -> int | None:
    peer = ADMIN_PEER.get()
    return None if peer is None else peer.uid


def policy_handlers(
    conn: sqlite3.Connection,
    *,
    registry: StagingRegistry,
    simulatable: Mapping[str, TxCommand[Any, Any]],
) -> dict[str, Handler]:
    def explain_(args: dict[str, Any]) -> dict[str, Any]:
        body = _bare(args, {"client_ref", "project_ref", "peer_ref"})
        client, project = body.get("client_ref"), body.get("project_ref")
        if not isinstance(client, str) or not isinstance(project, str):
            raise ValueError("client_ref and project_ref are required")  # noqa: TRY004 -- uniform ValueError on admin validation
        peer = body.get("peer_ref")
        if peer is not None and not isinstance(peer, str):
            raise ValueError("peer_ref must be a string")  # noqa: TRY004 -- uniform ValueError on admin validation
        return {"rows": explain(conn, client_ref=client, project_ref=project, peer_ref=peer)}

    def simulate(args: dict[str, Any]) -> dict[str, Any]:
        body = _bare(args, {"command", "args"})
        command = body.get("command")
        inner = body.get("args", {})
        if not isinstance(command, str) or command not in simulatable:
            raise ValueError("command cannot be simulated")
        if not isinstance(inner, dict):
            raise ValueError("args must be an object")  # noqa: TRY004 -- uniform ValueError on admin validation
        binding = current_binding(conn, _peer_uid())
        base = base_digest(conn)
        known = known_refs(conn)
        before, after = simulate_tx(conn, simulatable[command], inner, snapshot)
        diff = normalize_new_refs(diff_rows(before, after), known)
        handle = registry.stage(
            binding=binding,
            base_digest=base,
            payload={"command": command, "args": inner},
            diff=diff,
        )
        return {"staged": handle, "diff": diff}

    def diff_(args: dict[str, Any]) -> dict[str, Any]:
        body = _bare(args, {"staged"})
        handle = body.get("staged")
        if not isinstance(handle, str):
            raise ValueError("staged must be a tps_ handle")  # noqa: TRY004 -- uniform ValueError on admin validation
        staged = registry.get(
            handle, binding=current_binding(conn, _peer_uid()), current_base=base_digest(conn)
        )
        return {"staged": staged.handle, "diff": dict(staged.diff)}

    return {"policy explain": explain_, "policy simulate": simulate, "policy diff": diff_}
```

`client rotate` is not simulatable, and the test pins that. It is a credential operation with a file-system effect, not a policy change, and its effect on `EffectiveAccess` is nil.

- [ ] **Step 8: Run to verify they pass**

Run: `uv run pytest tests/unit/test_policy_handlers.py -q && uv run pytest -q`
Expected: 26 passed (1 + 16 + 9); full suite `+26` over the previous task's count, with the exact number recorded in the commit message.

If a `simulate_equals_commit_semantically` case fails, the failure is the finding. Print both diffs and fix the root cause, whether that is the command or the normaliser. Never drop the case.

- [ ] **Step 9: Commit**

```bash
git add src/telegram_mcp/ipc/admin.py src/telegram_mcp/authority/effective.py \
  src/telegram_mcp/storage/effective_access.py src/telegram_mcp/authority/staging.py \
  src/telegram_mcp/ipc/handlers/policy.py tests/unit/test_policy_handlers.py
git commit -m "feat: policy explain, simulate and diff; semantic diffs over every simulatable command; bound tps_ stages"
```

---

### Task 7: Audited lock/unlock, and audit verify / checkpoint / repair-anchor

**Files:**
- Create: `src/telegram_mcp/ipc/handlers/audit.py`
- Test: `tests/unit/test_audit_handlers.py`

**Interfaces:**
- Consumes: `run_audited_tx`, `AuditSink`, `admin_event`, `TxCommand` (Task 2), `insert_checkpoint`, `APPEND_GUARD` (Task 1), `write_epoch_state` (Task 1), and the existing `bind_epoch_state`, `set_locked`, `derive_integrity`, `repair_anchor`, `load_security` and `lookup_verification_key`.
- Also produces (review #6):
  - `chain.verify_checkpoints_registry(conn) -> str`, returning `"none"`, `"verified"` or `"failed"`. Each checkpoint verifies through **its own** `signing_key_id` in `verification_keys`, never through whichever key is currently loaded.
  - `disclosure.keys.ensure_current_published(conn, *, purpose, private_seed, now) -> str`, the single copy of "publish this Ed25519 key's public half if it is not current". `composition._disclosure_public` is refactored onto it in Task 11.
- Produces: `audit_handlers(conn, *, sink: AuditSink, checkpoint_key: bytes, on_security_change: Callable[[], None] | None = None) -> dict[str, Handler]`, with keys `lock`, `unlock`, `lock status`, `audit verify`, `audit checkpoint` and `audit repair-anchor`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_audit_handlers.py
"""Lock and audit commands through the audited runner (design §2.4, 0B G5, G16)."""

import secrets

import pytest

from telegram_mcp.disclosure.audit.anchor import CLEAN, derive_integrity, latch_degraded
from telegram_mcp.disclosure.audit.chain import verify_chain
from telegram_mcp.ipc.handlers._wrapper import AuditSink
from telegram_mcp.ipc.handlers.audit import audit_handlers
from telegram_mcp.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

KEY = secrets.token_bytes(32)
CHECKPOINT = secrets.token_bytes(32)
NOW = "2026-09-24T00:00:00Z"


@pytest.fixture
def world(tmp_path):
    from telegram_mcp.disclosure.keys import ensure_current_published

    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    ensure_current_published(conn, purpose="audit_checkpoint", private_seed=CHECKPOINT, now=NOW)
    anchor_dir = tmp_path / "anchor"
    anchor_dir.mkdir(mode=0o700)
    sink = AuditSink(KEY, anchor_dir / "anchor.json", lambda: NOW)
    return conn, sink, audit_handlers(conn, sink=sink, checkpoint_key=CHECKPOINT)


def _security(conn):
    return tuple(conn.execute("SELECT security_epoch, locked FROM security_state").fetchone())


def test_lock_and_unlock_bump_the_epoch_and_are_chained(world):
    conn, sink, h = world
    epoch, _ = _security(conn)
    assert h["lock"]({})["anchor"] == "refreshed"
    assert _security(conn) == (epoch + 1, 1)
    assert h["unlock"]({})["anchor"] == "refreshed"
    assert _security(conn) == (epoch + 2, 0)
    names = [r[0] for r in conn.execute("SELECT tool_name FROM audit_events ORDER BY chain_seq")]
    assert names == ["admin.lock", "admin.unlock"]
    verify_chain(conn, KEY)
    assert derive_integrity(conn, KEY, sink.anchor_path) == CLEAN


def test_lock_status_reports_without_writing(world):
    conn, _sink, h = world
    before = conn.total_changes
    status = h["lock status"]({})
    assert status == {"locked": False, "security_epoch": _security(conn)[0], "audit_degraded": False}
    assert conn.total_changes == before


def test_lock_is_refused_while_degraded_and_nothing_changes(world):
    conn, _sink, h = world
    latch_degraded(conn, reason="anchor_refresh_failure")
    before = _security(conn)
    with pytest.raises(PermissionError):
        h["lock"]({})
    assert _security(conn) == before


def test_checkpoint_signs_the_head_and_verify_reports_it(world):
    conn, _sink, h = world
    h["lock"]({})
    made = h["audit checkpoint"]({})
    assert made["chain_seq"] == 1
    report = h["audit verify"]({})
    assert report == {"integrity": CLEAN, "checkpoints": "verified", "audit_degraded": False}
    # registry-verified: the checkpoint names its own published key


def test_no_checkpoints_is_none_not_verified(world):
    """Global constraint: a check that could not run never reports success (review #6)."""
    _conn, _sink, h = world
    h["lock"]({})
    assert h["audit verify"]({})["checkpoints"] == "none"


def test_a_checkpoint_under_an_unpublished_key_fails(world):
    conn, _sink, h = world
    h["lock"]({})
    h["audit checkpoint"]({})
    conn.execute("DELETE FROM verification_keys WHERE purpose = 'audit_checkpoint'")
    conn.commit()
    assert h["audit verify"]({})["checkpoints"] == "failed"


def test_a_tampered_checkpoint_fails(world):
    conn, _sink, h = world
    h["lock"]({})
    h["audit checkpoint"]({})
    conn.execute("UPDATE audit_checkpoints SET last_event_mac = 'deadbeef'")
    conn.commit()
    assert h["audit verify"]({})["checkpoints"] == "failed"


def test_checkpoint_on_an_empty_chain_is_refused(world):
    _conn, _sink, h = world
    with pytest.raises(ValueError, match="empty"):
        h["audit checkpoint"]({})


def test_repair_is_refused_when_nothing_needs_repair(world):
    _conn, _sink, h = world
    h["lock"]({})
    with pytest.raises(PermissionError, match="not repairable"):
        h["audit repair-anchor"]({})


def test_an_anchor_failure_is_recovered_by_repair_then_writes_resume(world):
    """The operator path when a lock commits but its anchor cannot be refreshed.

    The command's commit stands, and every audited command is refused until
    `audit repair-anchor` succeeds. The chain is exactly one event ahead of
    the anchor, so repair is legal (RECOVERY_REQUIRED). This is the ordering
    the audit-degraded runbook (5c) documents.
    """
    import os

    conn, sink, h = world
    h["lock"]({})  # anchor written at seq 1
    os.chmod(sink.anchor_path.parent, 0o500)  # the next refresh cannot write
    try:
        assert h["unlock"]({})["anchor"] == "degraded"  # committed: now unlocked
    finally:
        os.chmod(sink.anchor_path.parent, 0o700)
    assert _security(conn)[1] == 0
    with pytest.raises(PermissionError):
        h["lock"]({})
    assert h["audit repair-anchor"]({})["integrity"] == CLEAN
    assert h["lock"]({})["anchor"] == "refreshed"


def test_the_security_change_hook_runs_after_commit(world, tmp_path):
    conn, sink, _h = world
    calls = []
    h = audit_handlers(
        conn, sink=sink, checkpoint_key=CHECKPOINT, on_security_change=lambda: calls.append(1)
    )
    h["lock"]({})
    assert calls == [1]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_audit_handlers.py -q`
Expected: FAIL with `ModuleNotFoundError: telegram_mcp.ipc.handlers.audit`.

- [ ] **Step 3: Implement**

```python
# src/telegram_mcp/ipc/handlers/audit.py
"""Lock and audit commands (spec §33, §23E, §43.8; Phase-5 design §2.4).

``lock``/``unlock`` apply the single epoch rule (``authority.set_locked``)
inside an audited transaction, so each is a chained ``admin.lock`` /
``admin.unlock`` event with a refreshed anchor. Before 5a nothing appended
these events (0B G16).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from telegram_mcp.authority.epochs import set_locked
from telegram_mcp.disclosure.audit.anchor import AnchorError, derive_integrity, repair_anchor
from telegram_mcp.disclosure.audit.chain import (
    APPEND_GUARD,
    ChainError,
    insert_checkpoint,
    verify_checkpoints_registry,
)
from telegram_mcp.ipc.handlers._wrapper import (
    AuditSink,
    Handler,
    TxCommand,
    admin_event,
    run_audited_tx,
)
from telegram_mcp.storage.authority_view import load_security
from telegram_mcp.storage.db import bind_epoch_state, write_epoch_state
from telegram_mcp.storage.settings import get_setting

__all__ = ["audit_handlers"]


def _no_args(args: dict[str, Any]) -> dict[str, Any]:
    if args:
        raise ValueError("unknown argument")
    return {}


def audit_handlers(
    conn: sqlite3.Connection,
    *,
    sink: AuditSink,
    checkpoint_key: bytes,
    on_security_change: Callable[[], None] | None = None,
) -> dict[str, Handler]:
    def lock_command(locked: bool) -> TxCommand[Any, Any]:
        def apply(conn: sqlite3.Connection, _plan: Any) -> dict[str, Any]:
            state = bind_epoch_state(conn)
            # Presence was verified by the router before this handler ran
            # (both commands are in PRESENCE_GATED).
            epoch = set_locked(state, locked, presence=True, now=sink.now())
            write_epoch_state(conn, state)
            return {"locked": locked, "security_epoch": epoch}

        return TxCommand(
            _no_args,
            lambda conn, parsed: parsed,
            apply,
            post_commit=(lambda _r: on_security_change()) if on_security_change else None,
        )

    def lock_(locked: bool) -> Handler:
        name = "admin.lock" if locked else "admin.unlock"
        return lambda args: run_audited_tx(
            conn,
            sink,
            lock_command(locked),
            args,
            event=lambda plan, result: admin_event(name, sink.now()),
        )

    def status(args: dict[str, Any]) -> dict[str, Any]:
        _no_args({k: v for k, v in args.items() if k != "presence"})
        epoch, locked = load_security(conn)
        return {
            "locked": locked,
            "security_epoch": epoch,
            "audit_degraded": bool(get_setting(conn, "audit.integrity_degraded")),
        }

    def checkpoint(args: dict[str, Any]) -> dict[str, Any]:
        def apply(conn: sqlite3.Connection, _plan: Any) -> dict[str, Any]:
            try:
                row = insert_checkpoint(conn, checkpoint_key, now=sink.now())
            except ChainError as exc:
                raise ValueError("cannot checkpoint an empty chain") from exc
            return {
                "checkpoint_ref": row["checkpoint_ref"],
                "chain_epoch": row["chain_epoch"],
                "chain_seq": row["chain_seq"],
            }

        command = TxCommand(_no_args, lambda conn, parsed: parsed, apply)
        return run_audited_tx(conn, sink, command, args, event=None)

    def verify(args: dict[str, Any]) -> dict[str, Any]:
        _no_args({k: v for k, v in args.items() if k != "presence"})
        return {
            "integrity": derive_integrity(conn, sink.chain_key, sink.anchor_path),
            "checkpoints": verify_checkpoints_registry(conn),
            "audit_degraded": bool(get_setting(conn, "audit.integrity_degraded")),
        }

    def repair(args: dict[str, Any]) -> dict[str, Any]:
        _no_args({k: v for k, v in args.items() if k != "presence"})
        with APPEND_GUARD:
            try:
                repair_anchor(
                    conn, sink.chain_key, checkpoint_key, sink.anchor_path, now=sink.now()
                )
            except AnchorError as exc:
                raise PermissionError("integrity state is not repairable") from exc
        return {"integrity": derive_integrity(conn, sink.chain_key, sink.anchor_path)}

    return {
        "lock": lock_(True),
        "unlock": lock_(False),
        "lock status": status,
        "audit checkpoint": checkpoint,
        "audit verify": verify,
        "audit repair-anchor": repair,
    }
```

Add these two functions **before** the handler module, test-first. The three new tests above fail until they exist.

In `disclosure/keys.py`:

```python
def ensure_current_published(
    conn: sqlite3.Connection, *, purpose: str, private_seed: bytes, now: str
) -> str:
    """Publish an Ed25519 key's public half unless it is already current. Returns the key_id."""
    import base64
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.from_private_bytes(private_seed).public_key().public_bytes_raw()
    key_id = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    current = current_verification_key(conn, purpose)
    if current is None or current["key_id"] != key_id:
        publish_verification_key(
            conn,
            key_id=key_id,
            purpose=purpose,
            algorithm="Ed25519",
            public_key_b64url=base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
            activated_at=now,
        )
    return key_id
```

`publish_verification_key` may commit by itself; read `keys.py:35-55` to check. Call `ensure_current_published` only outside a transaction, which is where both the fixture and composition call it.

In `disclosure/audit/chain.py`:

```python
def verify_checkpoints_registry(conn: sqlite3.Connection) -> str:
    """``"none"`` | ``"verified"`` | ``"failed"``: each checkpoint by its own recorded key."""
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from telegram_mcp.disclosure.keys import lookup_verification_key

    rows = conn.execute(
        "SELECT chain_epoch, chain_seq, last_event_id, last_event_mac, created_at, signature,"
        " signing_key_id FROM audit_checkpoints ORDER BY chain_epoch, chain_seq"
    ).fetchall()
    if not rows:
        return "none"
    names = (
        "chain_epoch",
        "chain_seq",
        "last_event_id",
        "last_event_mac",
        "created_at",
        "signature",
        "signing_key_id",
    )
    for row in rows:
        record = dict(zip(names, row, strict=True))
        key = lookup_verification_key(conn, record["signing_key_id"])
        if key is None or key["purpose"] != "audit_checkpoint":
            return "failed"
        raw = base64.urlsafe_b64decode(key["public_key_b64url"] + "=" * (-len(key["public_key_b64url"]) % 4))
        if "ed25519:sha256:" + hashlib.sha256(raw).hexdigest() != record["signing_key_id"]:
            return "failed"  # a stored label is never trusted by itself (spec §9.6.1)
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(
                bytes.fromhex(record["signature"]), _checkpoint_message(record)
            )
        except (InvalidSignature, ValueError):
            return "failed"
    return "verified"
```

Add it to `__all__`. `_checkpoint_message` reads only its own fields from the mapping, which you can confirm with `sed -n 269,280p src/telegram_mcp/disclosure/audit/chain.py`. The extra `signing_key_id` key is ignored. The old single-key `verify_checkpoints` stays for its existing callers.

`repair_anchor` opens its own transactions. It is a flow, not a `TxCommand`, and it lives in `disclosure/audit/`, outside the handler guard. It runs under the append guard.

The router passes `args` with `presence` still present, and `run_audited_tx` strips it before `parse`. The two read handlers strip it themselves.

**Recovery order when an audited command's anchor refresh fails:** the command's commit stands and the latch is set. Every audited command, including `unlock`, is then refused until `audit repair-anchor` runs, which is legal because the chain is exactly one event ahead of the anchor. This is intended fail-closed behaviour, not a trap. `test_an_anchor_failure_is_recovered_by_repair_then_writes_resume` pins it, and the 5c runbook documents it.

`PermissionError` from `repair_anchor` carries the text `not repairable`. The router shows it as `PERMISSION_DENIED: operation refused`, so the reason string never reaches the operator. The unit test calls the handler directly and sees the message.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_audit_handlers.py -q && uv run pytest -q`
Expected: 11 passed; full suite `+11`.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/ipc/handlers/audit.py src/telegram_mcp/disclosure/audit/chain.py \
  src/telegram_mcp/disclosure/keys.py tests/unit/test_audit_handlers.py
git commit -m "feat: audited lock and unlock; audit verify, checkpoint and repair-anchor"
```

---

### Task 8: Inspect commands and the restore-lineage seam

**Files:**
- Create: `src/telegram_mcp/disclosure/lineage.py`
- Create: `src/telegram_mcp/ipc/handlers/inspect.py`
- Test: `tests/unit/test_inspect_handlers.py`

**Interfaces:**
- Consumes: `verify_persisted_receipt`, `current_verification_key`, `lookup_verification_key`, `committed_usage`, `subject_digest`, `thresholds_for`, `window_start`, `BucketKey`, `GLOBAL`, `PROJECT`, `get_setting`, and a broker / prompter pair exposing `pending_count()` / `connected`.
- Produces:
  - `lineage.LineageVerdict(state: str)` and `lineage.NoRestoreLineage().affecting(conn, disclosure_ref) -> LineageVerdict`
  - `inspect_handlers(conn, *, lineage, broker, prompter, clock=time.time) -> dict[str, Handler]`, with keys `disclosure show`, `disclosure verify`, `disclosure key`, `exposure status` and `consent status`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_inspect_handlers.py
"""Operator inspection (spec §23A.3, §23C; design §2.4, §2.6)."""

import time

import pytest

from telegram_mcp.disclosure.lineage import LineageVerdict, NoRestoreLineage
from telegram_mcp.ipc.handlers.inspect import inspect_handlers
from telegram_mcp.storage.db import open_db
from tests.authority_fixtures import insert_committed_receipt, seed_authority_rows

CLIENT = "tcl_" + "a" * 26
REF = "tdr_" + "a" * 26


class Broker:
    def pending_count(self):
        return 2


class Prompter:
    connected = True


class Unreconstructable:
    def affecting(self, conn, disclosure_ref):
        return LineageVerdict("payload_unreconstructable")


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    insert_committed_receipt(c, disclosure_ref=REF, records=3, size=120)
    return c


def _h(conn, lineage=None):
    return inspect_handlers(
        conn, lineage=lineage or NoRestoreLineage(), broker=Broker(), prompter=Prompter()
    )


def test_show_is_privacy_minimised_and_reports_signature_state(conn):
    shown = _h(conn)["disclosure show"]({"disclosure_ref": REF})
    assert shown["records_disclosed"] == 3 and shown["bytes_disclosed"] == 120
    assert shown["signature"] == "invalid"  # the fixture row carries a placeholder signature
    assert shown["lineage"] == "none"
    assert shown["delivery"] == "committed"
    assert set(shown) == {
        "disclosure_ref",
        "committed_at",
        "tool_name",
        "project_count",
        "effective_egress_level",
        "records_disclosed",
        "bytes_disclosed",
        "partial",
        "proof_key_id",
        "signature",
        "lineage",
        "delivery",
    }


def test_lineage_distinguishes_restore_from_tampering(conn):
    shown = _h(conn, Unreconstructable())["disclosure show"]({"disclosure_ref": REF})
    assert shown["lineage"] == "payload_unreconstructable"
    assert _h(conn, Unreconstructable())["disclosure verify"]({"disclosure_ref": REF}) == {
        "signature_valid": False,
        "payload_reconstructable": False,
        "lineage": "payload_unreconstructable",
        "delivery": "committed",
    }


def test_a_withheld_disclosure_says_so(conn):
    """Review #7: Phase 3's degraded state stays visible (accounted, never delivered)."""
    from telegram_mcp.disclosure.audit.anchor import latch_degraded

    latch_degraded(conn, reason="anchor_refresh_failure", disclosure_ref=REF)
    shown = _h(conn)["disclosure show"]({"disclosure_ref": REF})
    assert shown["delivery"] == "withheld_audit_unavailable"
    assert _h(conn)["disclosure verify"]({"disclosure_ref": REF})["delivery"] == (
        "withheld_audit_unavailable"
    )


def test_a_withheld_disclosure_stays_withheld_after_repair(conn):
    """After repair the latch clears, but the chained repair event still names the ref."""
    conn.execute(
        "INSERT INTO audit_events (event_id, ts, tool_name, status, disclosure_ref, chain_epoch,"
        " chain_seq, prev_event_mac, event_mac) VALUES ('evt_1', '2026-09-24T00:00:00Z',"
        " 'admin.repair_anchor', 'ok', ?, 1, 1, 'p', 'm')",
        (REF,),
    )
    conn.commit()
    assert _h(conn)["disclosure show"]({"disclosure_ref": REF})["delivery"] == (
        "withheld_audit_unavailable"
    )


def test_unknown_receipts_are_refused(conn):
    with pytest.raises(ValueError, match="unknown"):
        _h(conn)["disclosure show"]({"disclosure_ref": "tdr_" + "b" * 26})


def test_disclosure_key_without_a_published_key_says_so(conn):
    assert _h(conn)["disclosure key"]({}) == {"key": None}


def test_exposure_status_sums_the_window(conn):
    conn.execute(
        "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id, budget_subject_kind,"
        " budget_subject_digest, records_disclosed, bytes_disclosed, effective_egress_level)"
        " VALUES (?, ?, 1, 'client_global', ?, 3, 120, 'metadata_only')",
        (REF, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), _global_digest()),
    )
    conn.commit()
    status = _h(conn)["exposure status"]({"client_ref": CLIENT})
    only = status["clients"][0]
    assert only["client_ref"] == CLIENT
    assert only["client_global"]["records"] == 3 and only["client_global"]["bytes"] == 120
    assert only["client_global"]["hard_records"] == 1500
    assert "project" not in only


def test_exposure_status_filters_are_optional(conn):
    """Review #9: `exposure status [--client] [--project]`, both optional (spec §33)."""
    everyone = _h(conn)["exposure status"]({})
    assert [c["client_ref"] for c in everyone["clients"]] == [CLIENT]
    from tests.authority_fixtures import PROJECT_REF

    by_project = _h(conn)["exposure status"]({"project_ref": PROJECT_REF})
    assert by_project["clients"][0]["project"]["records"] == 0
    with pytest.raises(ValueError):
        _h(conn)["exposure status"]({"client_ref": "tcl_" + "z" * 26})


def _global_digest():
    from telegram_mcp.disclosure.budget import GLOBAL, subject_digest

    return subject_digest(GLOBAL)


def test_consent_status(conn):
    assert _h(conn)["consent status"]({}) == {"agent_connected": True, "pending": 2}
```

`subject_digest` calls `load_key("privacy-key")`, which needs a key store. Add this autouse fixture at the top of the file, after the imports:

```python
@pytest.fixture(autouse=True)
def _keys(tmp_path):
    from telegram_mcp.keys.store import provision_missing, set_store_dir

    store = tmp_path / "keys"
    provision_missing(store, phases=(2, 3))
    set_store_dir(store)
```

First confirm the store API with `grep -n "def set_store_dir\|def provision_missing" src/telegram_mcp/keys/store.py`. Both appear at `store.py:111` and `:152`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_inspect_handlers.py -q`
Expected: FAIL with `ModuleNotFoundError: telegram_mcp.disclosure.lineage`.

- [ ] **Step 3: Implement `disclosure/lineage.py`**

```python
"""Restore lineage (Phase-5 design §2.6, §4.5).

5a ships the seam: nothing has been restored, so every receipt answers
``none``. 5c replaces this with the table-backed lookup, so a receipt whose
refs were re-minted by an authorised restore reports
``payload_unreconstructable`` instead of looking like tampering.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

__all__ = ["LineageVerdict", "NoRestoreLineage", "RestoreLineageLookup"]


@dataclass(frozen=True)
class LineageVerdict:
    state: str  # "none" | "payload_unreconstructable"


class RestoreLineageLookup(Protocol):
    def affecting(self, conn: sqlite3.Connection, disclosure_ref: str) -> LineageVerdict: ...


class NoRestoreLineage:
    def affecting(self, conn: sqlite3.Connection, disclosure_ref: str) -> LineageVerdict:
        return LineageVerdict("none")
```

- [ ] **Step 4: Implement `ipc/handlers/inspect.py`**

```python
"""Operator inspection: receipts, keys, exposure, consent (spec §23A.3, §23C, §33)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from telegram_mcp.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    committed_usage,
    subject_digest,
    thresholds_for,
    window_start,
)
from telegram_mcp.disclosure.keys import current_verification_key, lookup_verification_key
from telegram_mcp.disclosure.lineage import RestoreLineageLookup
from telegram_mcp.disclosure.verify import verify_persisted_receipt
from telegram_mcp.ipc.handlers._wrapper import Handler
from telegram_mcp.storage.settings import get_setting

__all__ = ["inspect_handlers"]

_SHOWN = (
    "disclosure_ref",
    "committed_at",
    "tool_name",
    "project_count",
    "effective_egress_level",
    "records_disclosed",
    "bytes_disclosed",
    "partial",
    "proof_key_id",
)


def _body(args: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - allowed:
        raise ValueError("unknown argument")
    return body


def inspect_handlers(
    conn: sqlite3.Connection,
    *,
    lineage: RestoreLineageLookup,
    broker: Any,
    prompter: Any,
    clock: Callable[[], float] = time.time,
) -> dict[str, Handler]:
    def _ref(args: dict[str, Any]) -> str:
        ref = _body(args, {"disclosure_ref"}).get("disclosure_ref")
        if not isinstance(ref, str) or not ref.startswith("tdr_"):
            raise ValueError("disclosure_ref must be a tdr_ ref")
        return ref

    def delivery(ref: str) -> str:
        """Phase-3 truth (design §6.8): accounted but withheld is not the same as handed off."""
        latched = get_setting(conn, "audit.integrity_degraded") and (
            get_setting(conn, "audit.degraded_disclosure_ref") == ref
        )
        repaired = conn.execute(
            "SELECT 1 FROM audit_events WHERE tool_name = 'admin.repair_anchor'"
            " AND disclosure_ref = ?",
            (ref,),
        ).fetchone()
        return "withheld_audit_unavailable" if latched or repaired else "committed"

    def show(args: dict[str, Any]) -> dict[str, Any]:
        ref = _ref(args)
        row = conn.execute(
            f"SELECT {', '.join(_SHOWN)} FROM disclosure_receipts WHERE disclosure_ref = ?",
            (ref,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown disclosure_ref")
        shown = dict(zip(_SHOWN, row, strict=True))
        shown["partial"] = bool(shown["partial"])
        shown["signature"] = "valid" if verify_persisted_receipt(conn, ref) else "invalid"
        shown["lineage"] = lineage.affecting(conn, ref).state
        shown["delivery"] = delivery(ref)
        return shown

    def verify(args: dict[str, Any]) -> dict[str, Any]:
        ref = _ref(args)
        if conn.execute(
            "SELECT 1 FROM disclosure_receipts WHERE disclosure_ref = ?", (ref,)
        ).fetchone() is None:
            raise ValueError("unknown disclosure_ref")
        state = lineage.affecting(conn, ref).state
        return {
            "signature_valid": verify_persisted_receipt(conn, ref),
            "payload_reconstructable": state != "payload_unreconstructable",
            "lineage": state,
            "delivery": delivery(ref),
        }

    def key(args: dict[str, Any]) -> dict[str, Any]:
        wanted = _body(args, {"key_id"}).get("key_id")
        if wanted is not None and not isinstance(wanted, str):
            raise ValueError("key_id must be a string")  # noqa: TRY004 -- uniform ValueError on admin validation
        found = (
            lookup_verification_key(conn, wanted)
            if wanted is not None
            else current_verification_key(conn, "disclosure_proof")
        )
        return {"key": dict(found) if found is not None else None}

    def exposure(args: dict[str, Any]) -> dict[str, Any]:
        body = _body(args, {"client_ref", "project_ref"})
        wanted = body.get("client_ref")
        clients = conn.execute(
            "SELECT id, client_ref FROM mcp_clients ORDER BY client_ref"
        ).fetchall()
        if wanted is not None:
            clients = [c for c in clients if c[1] == wanted]
            if not clients:
                raise ValueError("unknown client_ref")
        project = body.get("project_ref")
        if project is not None and conn.execute(
            "SELECT 1 FROM projects WHERE project_ref = ?", (project,)
        ).fetchone() is None:
            raise ValueError("unknown project_ref")
        minutes = get_setting(conn, "exposure_budget.rolling_window_minutes")
        since = window_start(clock(), minutes)

        def bucket(client_id: int, kind: str, subject: str = "") -> dict[str, Any]:
            used = committed_usage(
                conn, BucketKey(client_id, kind, subject_digest(kind, subject)), since=since
            )
            limits = thresholds_for(conn, kind)
            return {
                "records": used.records,
                "bytes": used.bytes,
                "soft_records": limits.soft_records,
                "hard_records": limits.hard_records,
                "soft_bytes": limits.soft_bytes,
                "hard_bytes": limits.hard_bytes,
            }

        rows = []
        for client_id, client_ref in clients:
            row: dict[str, Any] = {"client_ref": client_ref, GLOBAL: bucket(client_id, GLOBAL)}
            if project is not None:
                row[PROJECT] = bucket(client_id, PROJECT, project)
            rows.append(row)
        return {"window_minutes": minutes, "clients": rows}

    def consent(args: dict[str, Any]) -> dict[str, Any]:
        _body(args, set())
        return {"agent_connected": bool(prompter.connected), "pending": int(broker.pending_count())}

    return {
        "disclosure show": show,
        "disclosure verify": verify,
        "disclosure key": key,
        "exposure status": exposure,
        "consent status": consent,
    }
```

Confirm the `Usage` field names before running: `grep -n "class Usage" -A4 src/telegram_mcp/disclosure/budget.py`. If the fields are not `records` / `bytes`, use the shipped names. The test keys (`records`, `bytes`) belong to this handler's output, not to `Usage`.

`BucketKey` is constructed as `BucketKey(client_id, kind, subject_digest)` at `budget.py:130`.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/unit/test_inspect_handlers.py -q && uv run pytest -q`
Expected: 9 passed; full suite `+9`.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/disclosure/lineage.py src/telegram_mcp/ipc/handlers/inspect.py \
  tests/unit/test_inspect_handlers.py
git commit -m "feat: disclosure, exposure and consent inspection; restore-lineage seam"
```

---

### Task 9: The remaining project, scope and client commands

These are `project rename`, `project members`, `project remove-peer`, `project overlap`, `project instruction`, `project grant-cross-search`, `project revoke-cross-search`, `scope remove` and `client disable`.

**Files:**
- Modify: `src/telegram_mcp/ipc/handlers/projects.py` (rename, cross-search, overlap, instruction, members, remove-peer)
- Modify: `src/telegram_mcp/ipc/handlers/scope.py` (`scope remove`)
- Modify: `src/telegram_mcp/ipc/handlers/clients.py` (`client disable`)
- Test: `tests/unit/test_phase5a_project_commands.py`

**Interfaces:**
- Consumes: `TxCommand`, `tx_handler`, `DiscoveryStore`.
- Produces:
  - `projects.member_commands(store) -> {"project remove-peer": TxCommand}`
  - `PROJECT_COMMANDS` gains `project rename`, `project grant-cross-search` and `project revoke-cross-search`
  - `project_handlers(conn, *, members: DiscoveryStore | None = None)` gains `project members`, `project remove-peer`, `project overlap` and `project instruction`
  - `scope_commands` gains `scope remove`
  - `CLIENT_COMMANDS["client disable"]`, and `client_handlers` gains `client disable`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_phase5a_project_commands.py
"""Project, scope and client commands completed in 5a (design §2.4)."""

import pytest

from telegram_mcp.ipc.handlers.clients import client_handlers
from telegram_mcp.ipc.handlers.projects import PROJECT_COMMANDS, project_handlers
from telegram_mcp.storage.db import open_db
from telegram_mcp.telegram.discovery import DiscoveryStore
from tests.authority_fixtures import (
    BETA_REF,
    PROJECT_REF,
    seed_authority_rows,
    seed_project_world,
    seed_second_project,
)

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    seed_second_project(conn)
    return conn, refs, project_handlers(conn, members=DiscoveryStore())


def _epoch(conn, ref):
    return conn.execute(
        "SELECT project_epoch FROM projects WHERE project_ref = ?", (ref,)
    ).fetchone()[0]


def test_rename_validates_and_bumps_the_epoch(world):
    conn, _refs, h = world
    before = _epoch(conn, PROJECT_REF)
    h["project rename"]({"project_ref": PROJECT_REF, "display_name": "Ops"})
    assert _epoch(conn, PROJECT_REF) == before + 1
    with pytest.raises(ValueError):
        h["project rename"]({"project_ref": PROJECT_REF, "display_name": "a‮b"})


def test_members_then_remove_peer(world):
    conn, refs, h = world
    listed = h["project members"]({"project_ref": PROJECT_REF})["members"]
    assert {m["peer_ref"] for m in listed} == {refs["user:100"], refs["channel:7"], refs["chat:9"]}
    ali = next(m for m in listed if m["peer_ref"] == refs["user:100"])
    h["project remove-peer"]({"project_ref": PROJECT_REF, "handle": ali["handle"]})
    remaining = {m["peer_ref"] for m in h["project members"]({"project_ref": PROJECT_REF})["members"]}
    assert refs["user:100"] not in remaining


def test_member_handles_die_with_the_project_epoch(world):
    conn, _refs, h = world
    listed = h["project members"]({"project_ref": PROJECT_REF})["members"]
    h["project rename"]({"project_ref": PROJECT_REF, "display_name": "Moved"})
    with pytest.raises(ValueError):
        h["project remove-peer"]({"project_ref": PROJECT_REF, "handle": listed[0]["handle"]})


def test_a_member_handle_is_bound_to_its_project(world):
    _conn, _refs, h = world
    listed = h["project members"]({"project_ref": PROJECT_REF})["members"]
    with pytest.raises(ValueError):
        h["project remove-peer"]({"project_ref": BETA_REF, "handle": listed[0]["handle"]})


def test_overlap_reports_shared_peers_and_their_marking(world):
    _conn, refs, h = world
    overlap = h["project overlap"]({})["overlaps"]
    assert [o["peer_ref"] for o in overlap] == [refs["channel:7"]]
    kinds = {p["project_ref"]: p["membership_kind"] for p in overlap[0]["projects"]}
    assert kinds == {PROJECT_REF: "primary", BETA_REF: "shared"}
    assert overlap[0]["explicitly_shared"] is True


def test_overlap_takes_zero_or_two_known_refs(world):
    _conn, refs, h = world
    pair = h["project overlap"]({"project_a": PROJECT_REF, "project_b": BETA_REF})["overlaps"]
    assert [o["peer_ref"] for o in pair] == [refs["channel:7"]]
    for bad in (
        {"project_a": PROJECT_REF},
        {"project_a": PROJECT_REF, "project_b": PROJECT_REF},
        {"project_a": PROJECT_REF, "project_b": "tpr_" + "z" * 26},
    ):
        with pytest.raises(ValueError):
            h["project overlap"](bad)


def test_instruction_carries_the_ref_and_says_it_grants_nothing(world):
    _conn, _refs, h = world
    snippet = h["project instruction"]({"project_ref": PROJECT_REF, "target": "claude"})["snippet"]
    assert PROJECT_REF in snippet and "grants no access" in snippet
    with pytest.raises(ValueError):
        h["project instruction"]({"project_ref": PROJECT_REF, "target": "other"})


def test_cross_search_grant_and_revoke(world):
    conn, _refs, h = world
    h["project revoke-cross-search"]({"project_ref": PROJECT_REF, "client_ref": CLIENT})
    flag = "SELECT can_cross_search FROM client_projects WHERE project_id = 1"
    assert conn.execute(flag).fetchone()[0] == 0
    h["project grant-cross-search"]({"project_ref": PROJECT_REF, "client_ref": CLIENT})
    assert conn.execute(flag).fetchone()[0] == 1
    assert "project grant-cross-search" in PROJECT_COMMANDS


def test_client_disable(world, tmp_path):
    conn, _refs, _h = world
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    h["client disable"]({"client": "codex_local"})
    assert conn.execute("SELECT enabled FROM mcp_clients").fetchone()[0] == 0
    with pytest.raises(ValueError):
        h["client disable"]({"client": "openai_tunnel"})
```

Append this to `tests/unit/test_scope_handlers.py`:

```python
async def test_scope_remove_deletes_the_rule_and_bumps_the_epoch(world):
    conn, h = world
    found = await h["scope discover"]({})
    handle = next(s["handle"] for s in found["selections"] if s["display_name"] == "U0")
    h["scope allow"]({"handle": handle})
    found = await h["scope discover"]({})  # the allow moved the epoch
    handle = next(s["handle"] for s in found["selections"] if s["display_name"] == "U0")
    before = _epoch(conn)
    h["scope remove"]({"handle": handle})
    assert conn.execute("SELECT COUNT(*) FROM peer_policy").fetchone()[0] == 0
    assert _epoch(conn) == before + 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_phase5a_project_commands.py tests/unit/test_scope_handlers.py -q`
Expected: FAIL with `KeyError: 'project rename'`, `TypeError` on `project_handlers(..., members=...)`, and `KeyError: 'scope remove'`.

- [ ] **Step 3: Implement in `projects.py`**

Add the following above `PROJECT_COMMANDS`:

```python
def _parse_rename(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"project_ref", "display_name"})
    return {**body, "name": _display_name(body.get("display_name"))}


def _plan_project(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    return {**parsed, "project_id": _id(conn, "projects", "project_ref", parsed.get("project_ref"))}


def _apply_rename(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        "UPDATE projects SET display_name = ?, project_epoch = project_epoch + 1, updated_at = ?"
        " WHERE id = ?",
        (plan["name"], _now(), plan["project_id"]),
    )
    return {"display_name": plan["name"]}


def _cross(value: int) -> TxCommand[Any, Any]:
    def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        changed = conn.execute(
            "UPDATE client_projects SET can_cross_search = ?, updated_at = ?"
            " WHERE client_id = ? AND project_id = ?",
            (value, _now(), plan["client_id"], plan["project_id"]),
        ).rowcount
        if changed != 1:
            raise ValueError("no such grant")
        return {"can_cross_search": bool(value)}

    return TxCommand(lambda args: _args(args, {"project_ref", "client_ref"}), _pair, apply)
```

Add three entries to `PROJECT_COMMANDS`:

```python
    "project rename": TxCommand(_parse_rename, _plan_project, _apply_rename),
    "project grant-cross-search": _cross(1),
    "project revoke-cross-search": _cross(0),
```

Add the member handles and the read commands. Place `MemberView` at module level:

```python
@dataclass(frozen=True)
class MemberView:
    """What a ``project members`` handle resolves to. No reversible Telegram id."""

    project_ref: str
    peer_row_id: int
    peer_ref: str
    display_name: str | None
    chat_type: str
    username: str | None


_TARGETS = {"chatgpt": "ChatGPT project", "codex": "Codex workspace", "claude": "Claude Code workspace"}


def member_commands(store: DiscoveryStore) -> dict[str, TxCommand[Any, Any]]:
    """``project remove-peer`` as a TxCommand, so ``policy simulate`` covers it (review #3).

    ``store.take`` is non-consuming on success (``discovery.py:46-51``), so a
    simulated plan leaves the handle usable for the real command.
    """

    def parse_remove(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_ref", "handle"})
        if not isinstance(body.get("handle"), str) or not body["handle"].startswith("tgl_"):
            raise ValueError("handle must be a tgl_ selection from project members")
        return body

    def plan_remove(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, project_epoch FROM projects WHERE project_ref = ?",
            (parsed.get("project_ref"),),
        ).fetchone()
        if row is None:
            raise ValueError("unknown project_ref")
        view = store.take(parsed["handle"], policy_epoch=int(row[1]))
        if view.project_ref != parsed["project_ref"]:
            raise ValueError("unknown or expired selection")
        return {"project_id": int(row[0]), "peer_row_id": view.peer_row_id}

    def apply_remove(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        removed = conn.execute(
            "DELETE FROM project_peers WHERE project_id = ? AND peer_id = ?",
            (plan["project_id"], plan["peer_row_id"]),
        ).rowcount
        if removed != 1:
            raise ValueError("not a member")
        conn.execute(
            "UPDATE projects SET project_epoch = project_epoch + 1, updated_at = ? WHERE id = ?",
            (_now(), plan["project_id"]),
        )
        return {"removed": True}

    return {"project remove-peer": TxCommand(parse_remove, plan_remove, apply_remove)}
```

`from dataclasses import dataclass` must be imported. Then replace `project_handlers` with:

```python
def project_handlers(
    conn: sqlite3.Connection, *, members: DiscoveryStore | None = None
) -> dict[str, Handler]:
    store = members if members is not None else DiscoveryStore()

    def list_(args: dict[str, Any]) -> dict[str, Any]:
        _args(args, set())
        rows = conn.execute(
            "SELECT project_ref, slug, display_name, enabled, project_epoch FROM projects"
            " ORDER BY slug"
        ).fetchall()
        return {
            "projects": [
                {
                    "project_ref": r[0],
                    "slug": r[1],
                    "display_name": r[2],
                    "enabled": bool(r[3]),
                    "project_epoch": r[4],
                }
                for r in rows
            ]
        }

    def members_(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_ref"})
        row = conn.execute(
            "SELECT id, project_epoch FROM projects WHERE project_ref = ?",
            (body.get("project_ref"),),
        ).fetchone()
        if row is None:
            raise ValueError("unknown project_ref")
        views = [
            MemberView(body["project_ref"], int(r[0]), r[1], r[2], r[3], r[4])
            for r in conn.execute(
                "SELECT pe.id, pe.peer_ref, pe.display_name_cache, pe.telegram_peer_type,"
                " pe.username_cache FROM project_peers pp JOIN peers pe ON pe.id = pp.peer_id"
                " WHERE pp.project_id = ? ORDER BY pe.peer_ref",
                (row[0],),
            )
        ]
        listed = store.new_snapshot(views, policy_epoch=int(row[1]))
        for entry, view in zip(listed, views, strict=True):
            entry["peer_ref"] = view.peer_ref
        return {"members": listed}

    def overlap(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_a", "project_b"})
        given = [body.get("project_a"), body.get("project_b")]
        if sum(v is not None for v in given) == 1:
            raise ValueError("give both project_a and project_b, or neither")
        pair = {v for v in given if v is not None}
        for ref in pair:
            _id(conn, "projects", "project_ref", ref)  # unknown refs refuse
        if len(pair) == 1:
            raise ValueError("project_a and project_b must differ")
        grouped: dict[str, list[dict[str, str]]] = {}
        for peer_ref, project_ref, kind in conn.execute(
            "SELECT pe.peer_ref, p.project_ref, pp.membership_kind FROM project_peers pp"
            " JOIN projects p ON p.id = pp.project_id JOIN peers pe ON pe.id = pp.peer_id"
            " WHERE pp.peer_id IN (SELECT peer_id FROM project_peers GROUP BY peer_id"
            " HAVING COUNT(*) > 1) ORDER BY pe.peer_ref, p.project_ref"
        ):
            grouped.setdefault(peer_ref, []).append(
                {"project_ref": project_ref, "membership_kind": kind}
            )
        overlaps = [
            {
                "peer_ref": peer_ref,
                "projects": projects,
                "explicitly_shared": any(p["membership_kind"] == "shared" for p in projects),
            }
            for peer_ref, projects in sorted(grouped.items())
            if not pair or pair <= {p["project_ref"] for p in projects}
        ]
        return {"overlaps": overlaps}

    def instruction(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_ref", "target"})
        target = body.get("target")
        if target not in _TARGETS:
            raise ValueError("target must be chatgpt, codex or claude")
        row = conn.execute(
            "SELECT display_name FROM projects WHERE project_ref = ?", (body.get("project_ref"),)
        ).fetchone()
        if row is None:
            raise ValueError("unknown project_ref")
        snippet = (
            f"Telegram gateway project: {row[0]}\n"
            f"project_ref: {body['project_ref']}\n"
            f"Pass this project_ref to every Telegram tool call in this {_TARGETS[target]}.\n"
            "This note routes calls; it grants no access."
        )
        return {"snippet": snippet}

    return {
        **{name: tx_handler(conn, command) for name, command in PROJECT_COMMANDS.items()},
        "project list": list_,
        "project members": members_,
        "project remove-peer": tx_handler(conn, member_commands(store)["project remove-peer"]),
        "project overlap": overlap,
        "project instruction": instruction,
    }
```

Import `DiscoveryStore` from `telegram_mcp.telegram.discovery`. `DiscoveryStore.new_snapshot` reads `view.display_name`, `view.chat_type` and `view.username`, and `MemberView` provides all three.

`project remove-peer` lives in `member_commands(store)` rather than `PROJECT_COMMANDS`, because its plan needs the member store. Composition passes the **same** store to `project_handlers` (to mint handles) and to `member_commands` (to simulate and commit), so remove-peer is simulatable (review #3).

- [ ] **Step 4: Implement `scope remove` in `scope.py`**

Inside `scope_commands`, add:

```python
    def apply_remove(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        view = plan["view"]
        removed = conn.execute(
            "DELETE FROM peer_policy WHERE principal_id = ? AND account_id = ?"
            " AND telegram_peer_type = ? AND telegram_peer_id = ?",
            (plan["principal"], plan["account"], view.peer_type, view.peer_id),
        ).rowcount
        if removed != 1:
            raise ValueError("no rule for that chat")
        conn.execute(
            "UPDATE policy_state SET policy_epoch = policy_epoch + 1, updated_at = ?"
            " WHERE principal_id = ? AND account_id = ?",
            (_now(), plan["principal"], plan["account"]),
        )
        return {"removed": True}
```

Also add the returned entry `"scope remove": TxCommand(_parse_handle, plan_selection, apply_remove, post_commit=lambda _r: discovery.invalidate_all())`.

- [ ] **Step 5: Implement `client disable` in `clients.py`**

```python
def _parse_disable(args: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - {"client"}:
        raise ValueError("unknown argument")
    if body.get("client") not in _KINDS:
        raise ValueError("client must be codex_local or claude_code_local")
    return body


def _apply_disable(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    changed = conn.execute(
        "UPDATE mcp_clients SET enabled = 0 WHERE client_kind = ?", (plan["client"],)
    ).rowcount
    if changed != 1:
        raise ValueError("no such client")
    return {"disabled": True}


CLIENT_COMMANDS["client disable"] = TxCommand(_parse_disable, lambda c, p: p, _apply_disable)
```

Add `"client disable": tx_handler(conn, CLIENT_COMMANDS["client disable"])` to the dict that `client_handlers` returns, and import `tx_handler`.

Disabling does not bump `security_epoch`. §43.7 requires that only the affected client lose access. The evaluator's `client_enabled` step denies it on every call (`CLIENT_REVOKED`), and the other clients' leases stay valid.

- [ ] **Step 6: Run the targeted tests and the suite**

Run: `uv run pytest tests/unit/test_phase5a_project_commands.py tests/unit/test_scope_handlers.py -q && uv run pytest -q`
Expected: all pass; full suite `+10` over Task 5's count (9 project-command tests and 1 scope test). Record the exact number.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_mcp/ipc/handlers/{projects,scope,clients}.py \
  tests/unit/test_phase5a_project_commands.py tests/unit/test_scope_handlers.py
# Task 9 runs before Task 6 (see Task 6's execution-order note).
git commit -m "feat: project rename, members, remove-peer, overlap, instruction, cross-search; scope remove; client disable"
```

---

### Task 10: Touch ID summaries for admin approvals (0B G6)

**Files:**
- Create: `src/telegram_mcp/consent/admin_summaries.py`
- Modify: `src/telegram_mcp/consent/admin_approval.py:98-109`
- Test: `tests/unit/test_admin_summaries.py`

**Interfaces:**
- Consumes: `PRESENCE_GATED` (`ipc/admin.py`) and `SECRET_ARGS` (`admin_approval.py`).
- Produces: `admin_summaries.summarize(command: str, args: Mapping[str, Any]) -> str`, at most 160 codepoints. `AdminApprover.approve` uses it as `action_display`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_admin_summaries.py
"""Admin Touch ID prompts show what is being approved (design §2.7, 0B G6)."""

import pytest

from telegram_mcp.consent.admin_summaries import LIMIT, summarize
from telegram_mcp.ipc.admin import PRESENCE_GATED

LONG_REF = "tpr_" + "q" * 26
LONG_ARGS = {
    "project_ref": LONG_REF,
    "client_ref": "tcl_" + "r" * 26,
    "display_name": "N" * 80,
    "slug": "s" * 32,
    "egress_level": "excerpt",
    "excerpt_max_codepoints": 4000,
    "handle": "tgl_" + "h" * 26,
    "mode": "all_cloud_chats",
    "client": "claude_code_local",
    "target": "chatgpt",
    "phone": "+61400000000",
    "code": "12345",
    "password": "hunter2",
}
_CONTROLS = {*range(0x20), *range(0x7F, 0xA0), 0x200E, 0x200F, 0x061C, 0x2028, 0x2029}


@pytest.mark.parametrize("command", sorted(PRESENCE_GATED))
def test_every_gated_command_fits_and_renders_unchanged(command):
    text = summarize(command, LONG_ARGS)
    assert text.startswith(command)
    assert len(text) <= LIMIT
    assert not any(ord(c) in _CONTROLS for c in text)


def test_secrets_never_appear():
    text = summarize("auth login", LONG_ARGS)
    for secret in ("+61400000000", "12345", "hunter2"):
        assert secret not in text


def test_refs_are_shortened_and_salient_fields_shown():
    text = summarize("project set-egress", LONG_ARGS)
    assert "tpr_qqqq…qqqq" in text and "excerpt" in text and "4000" in text


def test_commands_without_salient_fields_stay_bare():
    assert summarize("lock", {}) == "lock"
    assert summarize("unlock", {"presence": {"token": "t"}}) == "unlock"
```

Append this to `tests/unit/test_admin_approval.py`. It uses the file's own `_world()`, whose fake agent appends every `(challenge, display)` pair to `seen` (`test_admin_approval.py:25-72`):

```python
async def test_the_prompt_shows_the_summary_not_just_the_command(tmp_path):
    approver, seen, tasks = await _world(tmp_path)
    await approver.approve("project disable", {"project_ref": "tpr_" + "q" * 26})
    assert seen[0][1]["action_display"] == "project disable · project tpr_qqqq…qqqq"
    for t in tasks:
        t.cancel()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_admin_summaries.py -q`
Expected: FAIL with `ModuleNotFoundError: telegram_mcp.consent.admin_summaries`.

- [ ] **Step 3: Implement**

```python
# src/telegram_mcp/consent/admin_summaries.py
"""What an admin Touch ID prompt says (Phase-5 design §2.7, 0B G6).

The display field set is frozen, so the summary rides in ``action_display``,
which the agent renders up to 160 codepoints (``consent-agent.swift``
``renderableText``). The exact arguments stay bound by ``request_hmac``; this
line is what the human reads before approving. Secrets never appear.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["LIMIT", "summarize"]

LIMIT = 160
_SECRETS = frozenset({"phone", "code", "password", "identity"})

# command -> (argument, label) pairs, in display order. Commands not listed
# show their name alone.
_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "client rotate": (("client", "client"),),
    "client disable": (("client", "client"),),
    "scope mode": (("mode", "mode"),),
    "scope allow": (("handle", "chat"),),
    "scope deny": (("handle", "chat"),),
    "scope remove": (("handle", "chat"),),
    "project create": (("slug", "slug"), ("display_name", "name")),
    "project rename": (("project_ref", "project"), ("display_name", "name")),
    "project enable": (("project_ref", "project"),),
    "project disable": (("project_ref", "project"),),
    "project add-peer": (("project_ref", "project"), ("handle", "chat")),
    "project remove-peer": (("project_ref", "project"), ("handle", "chat")),
    "project grant-client": (
        ("project_ref", "project"),
        ("client_ref", "client"),
        ("egress_level", "egress"),
        ("excerpt_max_codepoints", "max"),
    ),
    "project set-egress": (
        ("project_ref", "project"),
        ("client_ref", "client"),
        ("egress_level", "egress"),
        ("excerpt_max_codepoints", "max"),
    ),
    "project revoke-client": (("project_ref", "project"), ("client_ref", "client")),
    "project grant-cross-search": (("project_ref", "project"), ("client_ref", "client")),
    "project revoke-cross-search": (("project_ref", "project"), ("client_ref", "client")),
    "project instruction": (("project_ref", "project"), ("target", "for")),
}


def _short(value: Any) -> str:
    text = str(value)
    if len(text) > 17 and "_" in text[:5]:  # an opaque ref: prefix + 26
        return f"{text[:8]}…{text[-4:]}"
    return text if len(text) <= 40 else text[:39] + "…"


def summarize(command: str, args: Mapping[str, Any]) -> str:
    parts = [command]
    for name, label in _FIELDS.get(command, ()):
        if name in _SECRETS:
            continue
        value = args.get(name)
        if value is None:
            continue
        parts.append(f"{label} {_short(value)}")
    text = " · ".join(parts)
    return text if len(text) <= LIMIT else text[: LIMIT - 1] + "…"
```

`_short("tpr_" + "q"*26)` gives `"tpr_qqqq…qqqq"` (`text[:8]` + `…` + `text[-4:]`), and the test pins it.

In `admin_approval.py`, import `summarize` and change the display to use it:

```python
        display: dict[str, Any] = {
            "action_display": summarize(command, args),
            "client_display": "Operator (admin socket)",
            "peer_display": None,
            "project_display": [],
            "risk_class": "admin action",
        }
```

Add `"identity"` to `SECRET_ARGS` now, so the 5c recovery identity is bound by keyed hash from the first day it exists.

- [ ] **Step 4: Run to verify they pass, then the consent suites**

Run: `uv run pytest tests/unit/test_admin_summaries.py tests/unit/test_admin_approval.py tests/integration/test_phase4a_touch_id.py -q && uv run pytest -q`
Expected: all pass; full suite `+35` (31 + 3 + 1). `grep -rn action_display tests` at `6680090` finds no admin-approval assertion outside `test_admin_approval.py`, so no Phase-4 expectation should need to change. If a Phase-4 test asserts `action_display == command` for a command now listed in `_FIELDS`, update that assertion to the summary. It is the behaviour this task changes on purpose. Name every such test in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/consent/admin_summaries.py src/telegram_mcp/consent/admin_approval.py \
  tests/unit/test_admin_summaries.py tests/unit/test_admin_approval.py
git commit -m "feat: admin Touch ID prompts show a bounded summary of what is approved"
```

---

### Task 11: Wire it all, pin completeness by name, smoke, evidence

**Files:**
- Modify: `src/telegram_mcp/runtime/composition.py:188-212`
- Modify: `src/telegram_mcp/cli.py` (the `serve` verb)
- Modify: `scripts/e2e_smoke.py` (`phase5a_operator`, called from `main`)
- Create: `docs/verification/phase-5.md`
- Modify: `AGENT.md`, `CHANGELOG.md`
- Test: `tests/integration/test_phase5a_completeness.py`, `tests/integration/test_cli.py` (the `serve` test)

**Interfaces:**
- Consumes: everything above.
- Produces: `composition.admin_handlers(conn, *, key_dir, anchor_path, telegram, broker, prompter, seeds, runtime_id, clock) -> dict[str, Callable]`. `build_runtime` calls it. It is the only place the admin handler map is assembled, and it publishes the checkpoint key's public half so `audit verify` can resolve it (review #6).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_phase5a_completeness.py
"""Command completeness by name, not count (design D9, §5.4)."""

import secrets
import time

import pytest

from telegram_mcp.ipc.admin import ADMIN_COMMANDS, PRESENCE_GATED, AdminRouter
from telegram_mcp.keys.store import provision_missing, set_store_dir
from telegram_mcp.runtime.composition import admin_handlers
from telegram_mcp.storage.db import open_db
from telegram_mcp.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.authority_fixtures import seed_authority_rows
from tests.telegram.fake_client import FakeClient
from tests.unit.test_dialogs_and_discovery import _dialogs_result

CLI_ONLY = {"doctor", "serve"}
LATER_IN_PHASE_5 = {"auth revoke-this-session", "project drift", "policy export", "policy import"}
# Named, each with its reason (design D9):
#  tunnel rotate-binding -- Phase 6 (the CLI `rotate` already covers the pin)
#  release verify        -- Phase 7
#  consent approve       -- design rev 2 G7: spec line 927 makes it a MAY, a
#     generic admin prompt would approve a disclosure blind, and a pending
#     challenge exists only while its own prompt is in flight
#     (prompter.py:146-150), so there is nothing orphaned to approve.
DEFERRED = {"tunnel rotate-binding", "release verify", "consent approve"}


class _Broker:
    def pending_count(self):
        return 0

    def invalidate_where(self, predicate):
        return 0


class _Prompter:
    connected = False


@pytest.fixture
async def handlers(tmp_path):
    store = tmp_path / "keys"
    provision_missing(store, phases=(2, 3))
    set_store_dir(store)
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    (tmp_path / "anchor").mkdir(mode=0o700)
    fake = FakeClient({"messages.GetDialogsRequest": _dialogs_result()})
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    return admin_handlers(
        conn,
        key_dir=store,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        telegram=session,
        broker=_Broker(),
        prompter=_Prompter(),
        seeds=lambda ref: None,
        runtime_id=secrets.token_bytes(16),
        clock=time.time,
    )


async def test_exactly_the_named_commands_lack_a_handler(handlers):
    missing = set(ADMIN_COMMANDS) - set(handlers)
    assert missing == CLI_ONLY | LATER_IN_PHASE_5 | DEFERRED


async def test_every_missing_admin_command_answers_not_available(handlers):
    router = AdminRouter(handlers, presence_verifier=lambda proof: True)
    for command in sorted(LATER_IN_PHASE_5 | DEFERRED):
        response = await router.adispatch({"cmd": command, "args": {"presence": {}}})
        assert response["code"] == "NOT_AVAILABLE_IN_PHASE", command


def test_the_presence_set_is_unchanged_in_5a():
    from tests.unit.test_admin_handlers import MUTATING

    assert set(PRESENCE_GATED) == MUTATING  # by name, not count
```

In `tests/integration/test_cli.py` (in-process, via its `_run(argv, monkeypatch)` helper at :24), add `"serve"` to the `VERBS` tuple at :20, so `test_every_verb_has_help` covers it. Then add:

```python
def test_serve_points_at_start_and_exits_not_in_phase(monkeypatch, capsys):
    assert _run(["serve"], monkeypatch) == 5  # EXIT_NOT_IN_PHASE
    assert "telegram-mcp start" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/integration/test_phase5a_completeness.py -q`
Expected: FAIL with `ImportError: cannot import name 'admin_handlers'`.

- [ ] **Step 3: Implement `admin_handlers` in `composition.py`**

Add the imports:
- `AuditSink` from `telegram_mcp.ipc.handlers._wrapper`
- `audit_handlers` from `telegram_mcp.ipc.handlers.audit`
- `inspect_handlers` from `telegram_mcp.ipc.handlers.inspect`
- `policy_handlers` from `telegram_mcp.ipc.handlers.policy`
- `PROJECT_COMMANDS` from `telegram_mcp.ipc.handlers.projects`
- `scope_commands` from `telegram_mcp.ipc.handlers.scope`
- `CLIENT_COMMANDS` from `telegram_mcp.ipc.handlers.clients`
- `StagingRegistry` from `telegram_mcp.authority.staging`
- `NoRestoreLineage` from `telegram_mcp.disclosure.lineage`

Then add:

```python
def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def admin_handlers(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    telegram: Any,
    broker: Any,
    prompter: Any,
    seeds: Callable[[str], bytes | None],
    runtime_id: bytes,
    clock: Callable[[], float],
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """The one assembly point for the admin handler map (Phase-5 design §2)."""
    sink = AuditSink(load_key("audit-chain-key"), anchor_path, _iso_now)
    checkpoint_key = load_key("audit-checkpoint-key")
    ensure_current_published(
        conn, purpose="audit_checkpoint", private_seed=checkpoint_key, now=_iso_now()
    )
    discovery = DiscoveryStore()
    members = DiscoveryStore()  # one store: minted by `project members`, used by remove-peer
    simulatable: dict[str, Any] = {
        **PROJECT_COMMANDS,
        **CLIENT_COMMANDS,
        **member_commands(members),
    }
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        **project_handlers(conn, members=members),
        **client_handlers(conn, key_dir=key_dir),
        "auth headers": auth_headers_handler(
            conn, seed_for=seeds, runtime_id=runtime_id, clock=clock
        ),
        **audit_handlers(
            conn,
            sink=sink,
            checkpoint_key=checkpoint_key,
            on_security_change=lambda: broker.invalidate_where(lambda _p: True),
        ),
        **inspect_handlers(conn, lineage=NoRestoreLineage(), broker=broker, prompter=prompter),
    }
    if telegram is not None:
        handlers.update(auth_handlers(conn, telegram))
        handlers.update(scope_handlers(conn, telegram, discovery))
        simulatable.update(scope_commands(discovery))
    handlers.update(
        policy_handlers(conn, registry=StagingRegistry(), simulatable=simulatable)
    )
    return handlers
```

In `build_runtime`, replace the `handlers: dict[...] = {...}` block and the two `if telegram is not None:` updates with:

```python
    handlers = admin_handlers(
        conn,
        key_dir=key_dir,
        anchor_path=anchor_path,
        telegram=telegram,
        broker=broker,
        prompter=prompter,
        seeds=seeds.get,
        runtime_id=runtime_id,
        clock=clock,
    )
```

Also refactor `_disclosure_public` onto the single helper:

```python
def _disclosure_public(conn: sqlite3.Connection) -> tuple[str, str]:
    ident = ensure_current_published(
        conn,
        purpose="disclosure_proof",
        private_seed=load_key("disclosure-key"),
        now=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    current = current_verification_key(conn, "disclosure_proof")
    assert current is not None and current["key_id"] == ident
    return ident, current["public_key_b64url"]
```

Import `ensure_current_published` and `member_commands`. Remove the `publish_verification_key` and `Ed25519PrivateKey` imports from `composition.py` if they become unused.

`ConsentBroker.invalidate_where` exists (`broker.py:327`). Pending challenges carry `security_epoch`, so this eviction is cleanup, not the security boundary (design §2.1).

- [ ] **Step 4: Implement the `serve` verb in `cli.py`**

Add this to `_build_parser` next to `demo`:

```python
    sub.add_parser("serve", help="not a separate verb in this build; see start and demo")
```

Add the command function and register it in `_COMMANDS` as `"serve": _cmd_serve`:

```python
def _cmd_serve(args: argparse.Namespace) -> int:
    return _fail(
        "serve is not a separate verb in this build: use `telegram-mcp start`"
        " (runtime) or `telegram-mcp demo` (synthetic).",
        EXIT_NOT_IN_PHASE,
    )
```

Check `_fail`'s signature with `grep -n "def _fail" src/telegram_mcp/cli.py`. It takes `(message, code)` and prints to stderr.

- [ ] **Step 5: Run the targeted tests, then the whole suite**

Run: `uv run pytest tests/integration/test_phase5a_completeness.py tests/integration/test_cli.py -q && uv run pytest -q`
Expected: all pass; full suite `+5` over Task 10's count (3 completeness, 1 serve, 1 new `VERBS` case).

If `test_exactly_the_named_commands_lack_a_handler` reports extra missing names, each one is a command this plan wires and the composition missed. Fix the wiring, not the expected set.

- [ ] **Step 6: Add the smoke section**

In `scripts/e2e_smoke.py`, add `phase5a_operator(ledger)` after `phase4c_reads` in `main()`. It drives the real handlers over a real admin Unix socket:

```python
def phase5a_operator(ledger: Ledger) -> None:
    """Phase 5a over a real admin socket: simulate, diff, commit; lock round-trip; audit."""
    area = "Phase 5a — operator surface"
    import asyncio
    import secrets as _secrets

    sys.path.insert(0, str(REPO))
    from telegram_mcp.ipc.admin import AdminRouter, serve_admin
    from telegram_mcp.ipc.framing import decode_json_frame, encode_json_frame
    from telegram_mcp.keys.store import provision_missing, set_store_dir
    from telegram_mcp.runtime.composition import admin_handlers
    from telegram_mcp.storage.db import open_db
    from tests.authority_fixtures import (
        BETA_REF,
        seed_authority_rows,
        seed_project_world,
        seed_second_project,
    )

    results: dict[str, Any] = {}

    async def main() -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as short:
            root = Path(short)
            provision_missing(root / "keys", phases=(2, 3))
            set_store_dir(root / "keys")
            conn = open_db(root / "meta.db")
            seed_authority_rows(conn)
            seed_project_world(conn)
            seed_second_project(conn)
            (root / "anchor").mkdir(mode=0o700)

            class Broker:
                def pending_count(self) -> int:
                    return 0

                def invalidate_where(self, predicate: Any) -> int:
                    return 0

            class Prompter:
                connected = False

            handlers = admin_handlers(
                conn,
                key_dir=root / "keys",
                anchor_path=root / "anchor" / "anchor.json",
                telegram=None,
                broker=Broker(),
                prompter=Prompter(),
                seeds=lambda ref: None,
                runtime_id=_secrets.token_bytes(16),
                clock=time.time,
            )
            router = AdminRouter(handlers, presence_verifier=lambda proof: proof == {"m": "smoke"})
            sock = root / "admin.sock"
            server = await serve_admin(sock, router)
            try:

                async def call(cmd: str, **args: Any) -> dict[str, Any]:
                    reader, writer = await asyncio.open_unix_connection(str(sock))
                    payload = encode_json_frame({"cmd": cmd, "args": {"presence": {"m": "smoke"}, **args}})
                    writer.write(len(payload).to_bytes(4, "big") + payload)
                    await writer.drain()
                    size = int.from_bytes(await reader.readexactly(4), "big")
                    body = await reader.readexactly(size)
                    writer.close()
                    return decode_json_frame(body)

                simulated = await call(
                    "policy simulate", command="project disable", args={"project_ref": BETA_REF}
                )
                results["simulated"] = simulated["data"]["diff"]
                results["diff"] = (await call("policy diff", staged=simulated["data"]["staged"]))["data"]["diff"]
                await call("project disable", project_ref=BETA_REF)
                results["stale"] = (await call("policy diff", staged=simulated["data"]["staged"]))["code"]
                results["lock"] = (await call("lock"))["data"]
                results["status"] = (await call("lock status"))["data"]
                results["unlock"] = (await call("unlock"))["data"]
                results["verify"] = (await call("audit verify"))["data"]
                results["drift"] = (await call("project drift"))["code"]
            finally:
                server.close()
                await server.wait_closed()
                conn.close()

    def drive() -> dict[str, Any]:
        if not results:
            asyncio.run(main())
        return results

    ledger.run(area, "simulate then diff returns the staged change",
               lambda: drive()["simulated"] == drive()["diff"] or _raise("diff differs"))
    ledger.run(area, "a committed change makes the staged diff stale",
               lambda: drive()["stale"] == "MALFORMED_REQUEST" or _raise(drive()["stale"]))
    ledger.run(area, "lock is chained with a refreshed anchor",
               lambda: drive()["lock"]["anchor"] == "refreshed" or _raise(drive()["lock"]))
    ledger.run(area, "lock status reads locked without writing",
               lambda: drive()["status"]["locked"] is True or _raise(drive()["status"]))
    ledger.run(area, "unlock is chained with a refreshed anchor",
               lambda: drive()["unlock"]["anchor"] == "refreshed" or _raise(drive()["unlock"]))
    ledger.run(area, "audit verify reports CLEAN integrity",
               lambda: drive()["verify"]["integrity"] == "CLEAN" or _raise(drive()["verify"]))
    ledger.run(area, "a 5b command answers NOT_AVAILABLE_IN_PHASE",
               lambda: drive()["drift"] == "NOT_AVAILABLE_IN_PHASE" or _raise(drive()["drift"]))
```

If the smoke has no `_raise` helper yet, add one at module level:

```python
def _raise(detail: Any) -> None:
    raise AssertionError(str(detail))
```

`audit verify` on a chain with no checkpoints reports `"checkpoints": "none"`, never `"verified"` (review #6). The CLEAN row checks `integrity` only, and the ledger detail prints the whole report.

`serve_admin` with no `allow_uid` accepts the running euid, which is the smoke's own process.

- [ ] **Step 7: Run the smoke**

Run: `uv run python scripts/e2e_smoke.py`
Expected: `60 passed, 0 failed` (53 + 7), exit 0. If the agent bundle is not built, the earlier sections report their existing skips and the count shifts accordingly. Record the exact line.

- [ ] **Step 8: Write the evidence and the audit trail**

Create `docs/verification/phase-5.md` with a `## 5a` section in the established format of `phase-4.md`. Read that file's headings first and copy its table shape. It must contain:
- the full gate output line for each gate command;
- a gate ledger in which F, H, O, P, Q and R each get one line naming what 5a adds and the proving test file, with every gate still **PARTIAL** and what is missing named (5b/5c, Phase 6/7);
- the shipped defects fixed in 5a: the duplicate `scope mode`, `client rotate` re-enabling a disabled client, the unsynced seed write, `ensure_peer` outside the handler transaction, and lock/unlock unchained;
- the shipped gaps recorded for 5b: `write_checkpoint` has no caller (§26.5 cadence unenforced), `purge_expired_cursors` has no caller (G10), and the `UserDeactivated` mapping (§3.2);
- the named deferred set and why.

Append the dated `**Raouf:**` entry to `AGENT.md` and `CHANGELOG.md`, with Scope, Summary, Files changed, Verification (the exact counts) and Follow-ups (the 5b plan).

- [ ] **Step 9: Run the full gate, then commit**

Run each command separately and read each exit status. None may be piped:

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest -q
uv run python scripts/e2e_smoke.py
uv run pytest tests/formal -q -s
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
uv build
```

Expected: every command exits 0. Formal stays at `624 states, 18 assertions`, since 5a does not touch the model.

```bash
git add src/telegram_mcp/runtime/composition.py src/telegram_mcp/cli.py scripts/e2e_smoke.py \
  tests/integration/test_phase5a_completeness.py tests/integration/test_cli.py \
  docs/verification/phase-5.md AGENT.md CHANGELOG.md
git commit -m "feat: wire the 5a operator surface; completeness by name; smoke and evidence"
```

---

## Revision 3 (owner's review of revision 2)

Each finding was checked against the code before being adopted or rejected. Ten were adopted, one was rejected with a test added anyway, and one was rejected on the record.

| # | Finding | Checked against | Ruling | Where |
|---|---|---|---|---|
| 1 | Retrieval decides chat class outside the evaluator | `reads.py:123,231,386,676` call `owner_scope.admits` after a fact-free `evaluate` | **Adopted**; design §2.2 required it and rev 2 dropped it | New Task 4A: `admit_live`, snapshots carry `view`, guard on `.admits`/`.decide` |
| 2 | Simulation consumes `tgl_` handles | `discovery.py:46-51`: `take()` pops only **invalid** handles, and a valid take leaves the handle in place | **Claim rejected**; its test was adopted to pin the behaviour | Task 6 `test_simulation_leaves_a_selection_handle_usable` |
| 3 | Simulation is incomplete, and minting makes it unequal | `mint_opaque_ref` in `project create` / `ensure_peer_in_tx` during a simulated apply; rev 2's cases avoided both | **Adopted** | Task 6 semantic `normalize_new_refs`; all 16 simulatable commands parametrised, with a guard test that the case list equals the command set; Task 9 `member_commands` makes remove-peer simulatable |
| 4 | `tps_` not bound to operator/account | Rev 2 `Staged` had base only | **Adopted** | Task 6 `Binding(peer_uid, principal_ref, account_ref, security_epoch)` via `ADMIN_PEER`; cap of 64; a mismatch never deletes the stage |
| 5 | `consent approve` wrongly deferred | Premise "Section 1 wired it" is superseded by design rev 2 G7, which the owner approved. `prompter.py:146-150`: agent loss denies every waiter, and a challenge exists only while its prompt is in flight | **Rejected on the record**; the reason is written into the completeness test | Task 11 `DEFERRED` comment |
| 6 | `audit verify` uses the current key and calls zero checkpoints "verified" | Rev 2 Task 7 code | **Adopted** | `verify_checkpoints_registry` returns `none`/`verified`/`failed` per recorded key; `ensure_current_published` is the single copy |
| 7 | `disclosure show` loses the withheld state | `audit.degraded_disclosure_ref`; `admin.repair_anchor` events carry `disclosure_ref` (`anchor.py:215-235`) | **Adopted** | Task 8 `delivery`; `verify` splits `signature_valid` / `payload_reconstructable` / `lineage` / `delivery` |
| 8 | Unknown class reads as unconditional `allow`; diffs miss class changes | `_COMPARED` omitted it; `decide` returned `None` for a stored channel even with groups and channels both off | **Adopted** | Task 5 `owner_class` column, which is diffed; Task 4 `PeerFacts.candidates` |
| 9 | `exposure status` needs a client | Frozen §33: both filters optional | **Adopted** | Task 8: per-client list, optional project |
| 10 | Rotation mutates the credential on failure | Rev 2 wrote the live seed before the TX | **Adopted** | Task 3 `.next` pending seed, activated only after commit; busy-DB test proves the live seed is untouched |
| 11 | `scope list` / `client list` accept arguments | Code | **Adopted** | Task 3 |
| 12 | `project overlap` takes 0, 1 or unknown refs | Code | **Adopted** | Task 9: zero or two known, distinct refs |
| — | Presence pinned by count | Task 11 used `len == 31` | **Adopted** | By name against `test_admin_handlers.MUTATING` |

## Gauntlet (revision 2 of this plan)

After it was written, the plan was checked line by line against the tree at `6680090`, by execution where possible. The run found 15 defects and 2 predictions confirmed; all 15 defects are fixed in place above.

| # | Sev | Defect | Evidence | Fix |
|---|---|---|---|---|
| P1 | high | Review Focus #1 was mis-tested. Under lock contention, `BEGIN IMMEDIATE` raises `OperationalError` after about 5.2 s, which surfaces as `INTERNAL_ERROR`. The named test covered an apply failure instead. | Probe: second connection holds `BEGIN IMMEDIATE` → `OperationalError: database is locked` after 5.2 s, `in_transaction` False | `_transaction` / `simulate_tx` map contention to `ValueError(BUSY)`, with a real two-connection test |
| P2 | high | The audited-command anchor failure had no recovery test. Once degraded, even `unlock` is refused until repair, and that was undocumented. | `run_audited_tx` refuses while degraded | New test `…recovered_by_repair_then_writes_resume` plus a note |
| P3 | medium | `_short` shipped a known-wrong slice with a "correct it" note. | Plan text | Fixed to `text[:8]` |
| P4 | medium | `explain` contained a nonsense `targets` expression with a "use the other form" note. | Plan text | Replaced inline |
| P5 | medium | `_row` used three `type: ignore`s. | Plan text | `isinstance` narrowing |
| P6 | medium | The `client list` body did not match the shipped one (bearer filter, no `rotated_at`). | `clients.py:62-71` | Shipped body verbatim |
| P7 | medium | The `serve` CLI test assumed a subprocess helper. | `test_cli.py` is in-process: `_run` at :24, `VERBS` at :20 | `_run` plus capsys; `VERBS` extended |
| P8 | medium | The approval test was a placeholder ("adapt to existing fixtures"). | `_world()` records displays in `seen` | Exact test |
| P9 | medium | Test counts were wrong (Task 2 has 11 + 1, not 12; Task 6 has 9, not 10), and several full-suite counts were missing. | Counted | Every task states a cumulative full-suite count |
| P10 | low | Hedged about `StorageError`'s base class. | `db.py:70`: `Exception` | Decided |
| P11 | low | `extra: set[str] = frozenset()` fails mypy. | Type rules | `frozenset[str]` |
| P12 | low | The `hasattr`-guarded hook plus a "replace this" note. | `broker.py:327` | Direct call; the fake gains the method |
| P13 | low | "`ADMIN_EVENTS` is already exported" was false. | `chain.__all__` lacks it | Added in Task 1 |
| P14 | low | The raw-identity test was weak: `json.dumps` spacing meant `":7,"` could never match. | Probe of `json.dumps` separators | Walks every produced value |
| P17 | medium | Two code blocks were placeholders: `list_` said `# ... unchanged from Task 3 ...`, and the approval display dict used `...`. | `ast.parse` over all 51 python blocks: 3 unparseable, 1 of them an intentional dict-entry fragment | Both written out in full. All blocks now parse, directly or as the dict-entry fragment they are. |
| P15 | ok | The `await`-in-transaction guard was predicted empty today. | Ran the guard at `6680090`: `[]` | None |
| P16 | ok | The decision-attribute guard was predicted to flag only `seams.py:160-166`. | Ran it: exactly those four lines | None |

Also confirmed by execution: the `SAVEPOINT` / `ROLLBACK TO` / `RELEASE` / `rollback` sequence on the real `open_db` connection restores the row and leaves no transaction open; `write_anchor` into a missing directory raises `FileNotFoundError` (an `OSError`), which the audited runner catches; `jcs_dumps` accepts `null` and booleans; `Usage` fields are `records` / `bytes`; `GLOBAL == "client_global"`; `write_checkpoint`'s two callers (`test_audit_chain.py:178,192`) run outside a transaction, so the new wrapper is safe for them; and the admin frame header is 4 bytes, matching the smoke's `call`.

## Self-review (done while writing)

- **Spec coverage.** Design §2.1 → Tasks 2 and 3. §2.2 → Task 4. §2.3 → Tasks 5 and 6. §2.4 table → Tasks 7, 8 and 9 (every row). §2.5 → Task 11 (`test_the_presence_set_is_unchanged_in_5a`). §2.6 → Task 8. §2.7 → Task 10. 0B G4 → Task 3 guards. G5 → Tasks 1, 2 and 7. G6 → Task 10. G7 → Task 11 `DEFERRED`. G11 → Task 3. G12 → Task 2. G13 → Task 4 guard. G14 → Tasks 4 and 5. G16 → Task 7. D9 / §5.4 → Task 11. G1–G3, G8–G10 and G15 belong to 5b/5c and are not in this plan.
- **Known limitation, recorded in evidence.** `project remove-peer` is not simulatable in 5a, because its plan depends on the member-handle store, which `policy simulate` cannot mint through. `scope allow/deny/remove` and `project add-peer` can be simulated with a live `tgl_` handle.
- **Type consistency.** `TxCommand(parse, plan, apply, post_commit)`, `run_tx`, `simulate_tx(conn, command, args, observe)`, `run_audited_tx(conn, sink, command, args, *, event, allow_degraded)`, `AccessRow.key` and `diff_rows(before, after)` are used with the same signatures in every task.
- **No placeholders.** Four steps tell the engineer to *read* a shipped file before editing, because the plan cannot pin its exact current shape: `client list` output keys, the `Usage` field names, the test helper in `test_admin_approval.py`, and the CLI runner in `test_cli.py`. Each names the grep that settles it.
