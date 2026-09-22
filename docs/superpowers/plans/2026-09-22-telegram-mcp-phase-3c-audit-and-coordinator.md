# Telegram MCP Phase 3c — Audit Chain and Disclosure Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose Plans 3a and 3b into one atomic disclosure transaction — a MAC-linked audit chain, an external head anchor, a latched degraded state with a real recovery ceremony, and the coordinator that is the only thing in the system able to release a sensitive payload.

**Architecture:** `audit/chain.py` appends MAC-linked events under `BEGIN IMMEDIATE`; `audit/anchor.py` owns a `0600` file anchor with a domain-separated MAC and a durable write sequence; `coordinator.py` runs the twelve steps and holds a single process-wide `audit_append_guard` across steps 11 and 12 so nothing can append between the database commit and the anchor refresh. The coordinator sequences and never decides: authority, consent, budget and integrity decisions stay in their own modules.

**Tech Stack:** Python 3.12, stdlib `sqlite3`, `hmac`, `os`, `threading`, `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md` (revision 3). Read §1.2, §2, §6, §9.2 and §10 in full before starting. The frozen contract is `telegram-mcp-v0.1.10-final-engineering-spec.md` §12.2, §23A.3 and §26.5.

**Depends on:** Plan 3a (all tasks) and Plan 3b (all tasks).

## Global Constraints

- **Never commit or log Telegram credentials, session files, keys or private material.** No message body, search text, username, phone number or raw Telegram ID may reach `audit_events`, `audit_checkpoints`, `disclosure_receipts`, `exposure_ledger`, the settings table or any log line.
- **No production claim** until Gates A–R pass. Phase 3c moves parts of Gates O, P and Q.
- **Fail closed.** Integrity that cannot be established refuses; it never permits.
- **No sensitive byte may cross the process boundary before `refresh_anchor` succeeds.** Every sensitive response is fully materialised in memory before step 12.
- **No test-only flags in production paths.** The clock, the adapter and the anchor path are injected seams.
- **One copy of each shared rule.** `jcs_dumps`, `measure.py` and `budget.py` are imported, never reimplemented.
- **Uniform `ValueError` on validation**, with `# noqa: TRY004 -- <reason>` where ruff objects.
- **Read `AGENT.md` and `CHANGELOG.md` before editing**, and append a dated `**Raouf:**` entry to both afterwards.
- Verification gate for every task: `uv run pytest -q`, `uv run python scripts/e2e_smoke.py`, `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src/telegram_mcp`.

## File Structure

| File | Responsibility |
|---|---|
| `src/telegram_mcp/disclosure/audit/__init__.py` | package marker |
| `src/telegram_mcp/disclosure/audit/chain.py` | event MAC, genesis, atomic append, checkpoints, verify |
| `src/telegram_mcp/disclosure/audit/anchor.py` | anchor file format, durable refresh, integrity derivation, degraded latch |
| `src/telegram_mcp/disclosure/coordinator.py` | `DISCLOSURE_STEPS`, the append guard, the twelve steps |
| `src/telegram_mcp/storage/settings.py` | eleven new registry rows |
| `tests/unit/test_audit_chain.py` | MAC, genesis, linearity, checkpoints |
| `tests/unit/test_audit_anchor.py` | format, durability, rejection matrix, integrity table |
| `tests/integration/test_disclosure_coordinator.py` | the twelve steps and the crash model |
| `tests/security/test_no_content_in_stores.py` | Gate O's first privacy MUST |
| `tests/security/test_no_uncommitted_escape.py` | no byte before the anchor |
| `src/telegram_mcp/disclosure/verify.py` | rebuild a persisted receipt and check it (Appendix K.2) |
| `formal/README.md`, `formal/model.md` | Appendix L obligations |

---

### Task 1: The eleven settings rows

**Files:**
- Modify: `src/telegram_mcp/storage/settings.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- Produces: eleven new `SETTINGS_REGISTRY` keys — `audit.integrity_degraded`, `audit.degraded_disclosure_ref`, `audit.degraded_reason`, `audit.external_anchor_provider`, `audit.external_anchor_ref`, and `retention.{disclosure_receipt_days, exposure_ledger_days, audit_events_days, audit_checkpoint_days, verification_key_grace_days, message_ref_days}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_storage.py`:

```python
def test_phase_three_settings_rows_exist_with_the_spec_defaults(conn):
    assert get_setting(conn, "audit.integrity_degraded") == 0
    assert get_setting(conn, "audit.external_anchor_provider") == "file_0600"
    assert get_setting(conn, "retention.disclosure_receipt_days") == 180
    assert get_setting(conn, "retention.exposure_ledger_days") == 30
    assert get_setting(conn, "retention.audit_events_days") == 30
    assert get_setting(conn, "retention.audit_checkpoint_days") == 180
    assert get_setting(conn, "retention.verification_key_grace_days") == 30
    assert get_setting(conn, "retention.message_ref_days") == 180


def test_exposure_ledger_retention_cannot_be_shorter_than_the_window():
    # Spec §23C.1: retention MUST be at least the longest rolling window.
    # The window is capped at 1440 minutes, which is one day.
    with pytest.raises(ValueError):
        validate_setting("retention.exposure_ledger_days", 0)


def test_degraded_reason_is_a_closed_set():
    assert validate_setting("audit.degraded_reason", "anchor_refresh_failure")
    with pytest.raises(ValueError):
        validate_setting("audit.degraded_reason", "something happened")


def test_degraded_disclosure_ref_accepts_only_a_tdr_ref():
    assert validate_setting("audit.degraded_disclosure_ref", "tdr_" + "a" * 26)
    with pytest.raises(ValueError):
        validate_setting("audit.degraded_disclosure_ref", "the invoice thread")


def test_anchor_provider_is_a_closed_set():
    with pytest.raises(ValueError):
        validate_setting("audit.external_anchor_provider", "dropbox")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_storage.py -k phase_three -v`
Expected: FAIL with `ValueError` for an unknown settings key.

- [ ] **Step 3: Write minimal implementation**

In `src/telegram_mcp/storage/settings.py`, add before the `release.*` rows:

```python
    # --- Phase 3: audit integrity (design §6.3, §6.8) ----------------------
    # A tdr_ ref and a fixed reason code are not content, so the registry's
    # prohibition is respected: neither can carry a message body or a query.
    "audit.integrity_degraded": SettingSpec("int", 0, minimum=0, maximum=1, origin="spec", phase=3),
    "audit.degraded_disclosure_ref": SettingSpec(
        "str", "", pattern=_TDR_OR_EMPTY, origin="spec", phase=3
    ),
    "audit.degraded_reason": SettingSpec(
        "str", "", choices=("", *_DEGRADED_REASONS), origin="spec", phase=3
    ),
    # The spec's reference config declares macos_system_keychain; this build
    # uses the reviewed 0600-file fallback, so the declaration follows it.
    # A manifest that claims a provider the build does not use is a lie in
    # the artifact that exists to prevent lies.
    "audit.external_anchor_provider": SettingSpec(
        "str", "file_0600", choices=_ANCHOR_PROVIDERS, origin="impl", phase=3
    ),
    "audit.external_anchor_ref": SettingSpec(
        "str", "audit-head-anchor.json", origin="impl", phase=3
    ),
    # --- Phase 3: retention (spec §32 privacy block) -----------------------
    # Phase 3 sets the values; Phase 5 enforces the purge schedules and must
    # not be handed values it cannot satisfy.
    "retention.disclosure_receipt_days": SettingSpec("int", 180, minimum=1, maximum=3_650, phase=3),
    "retention.exposure_ledger_days": SettingSpec("int", 30, minimum=1, maximum=3_650, phase=3),
    "retention.audit_events_days": SettingSpec("int", 30, minimum=1, maximum=3_650, phase=3),
    "retention.audit_checkpoint_days": SettingSpec("int", 180, minimum=1, maximum=3_650, phase=3),
    "retention.verification_key_grace_days": SettingSpec("int", 30, minimum=1, maximum=3_650, phase=3),
    "retention.message_ref_days": SettingSpec("int", 180, minimum=1, maximum=3_650, phase=3),
```

Add near the other module constants:

```python
_DEGRADED_REASONS = ("anchor_refresh_failure", "chain_verify_failure", "anchor_read_failure")
_ANCHOR_PROVIDERS = ("file_0600", "macos_system_keychain")
_TDR_OR_EMPTY = re.compile(r"(tdr_[a-z2-7]{26})?\Z")
```

Extend `SettingSpec` and `validate_setting`:

```python
@dataclass(frozen=True)
class SettingSpec:
    kind: str
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] | None = None
    pattern: re.Pattern[str] | None = None
    origin: str = "spec"
    phase: int = 2
```

and in the string branch of `validate_setting`, beside the existing `choices`
check:

```python
        if spec.pattern is not None and spec.pattern.fullmatch(value) is None:
            raise ValueError("setting value does not match its allowed shape")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_storage.py -v`
Expected: PASS. `test_settings_defaults_match_the_spec_baseline` asserts `set(all_settings(conn)) == set(SETTINGS_REGISTRY)`, so it covers the new rows automatically.

- [ ] **Step 5: Confirm no new row can carry content**

Run: `uv run pytest tests/unit/test_storage.py::test_no_registry_key_can_carry_telegram_content -v`
Expected: PASS — no new key contains `query`, `message`, `body`, `caption`, `username`, `phone`, `url` or `text`.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/storage/settings.py tests/unit/test_storage.py
git commit -m "feat: add the eleven Phase-3 settings rows"
```

---

### Task 2: The audit chain

**Files:**
- Create: `src/telegram_mcp/disclosure/audit/__init__.py`, `src/telegram_mcp/disclosure/audit/chain.py`
- Test: `tests/unit/test_audit_chain.py`

**Interfaces:**
- Consumes: `jcs_dumps`; `load_key` from `telegram_mcp.keys.store`.
- Produces:
  - `EVENT_DOMAIN = b"telegram-mcp-audit-v1"`, `GENESIS_DOMAIN = b"telegram-mcp-audit-genesis-v1"`
  - `ADMIN_EVENTS: tuple[str, ...]` — the closed dotted vocabulary.
  - `mint_event_id() -> str` — `evt_` + 26-char Crockford base32 ULID.
  - `genesis_mac(chain_epoch: int) -> str`
  - `event_mac(chain_key: bytes, *, chain_epoch, chain_seq, prev_event_mac, event) -> str`
  - `append_event(conn, chain_key, event: Mapping[str, Any]) -> dict[str, Any]`
  - `head(conn) -> dict[str, Any] | None`
  - `verify_chain(conn, chain_key) -> None` raising `ChainError`
  - `ChainError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_audit_chain.py`:

```python
"""MAC-linked audit chain (design §6.1, §6.2; frozen spec §26.5)."""

import pytest

from telegram_mcp.disclosure.audit.chain import (
    ADMIN_EVENTS,
    ChainError,
    append_event,
    event_mac,
    genesis_mac,
    head,
    mint_event_id,
    verify_chain,
)
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate

_KEY = bytes(range(32))


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "meta.db")
    migrate(connection)
    return connection


def _append(conn, event):
    """Every append runs inside a caller-owned transaction; there is no
    convenience path that commits for you, because the coordinator must be
    able to put the receipt and the ledger rows in the same transaction."""
    from telegram_mcp.disclosure.audit.chain import immediate_transaction

    with immediate_transaction(conn):
        return append_event(conn, _KEY, event)


def _event(**overrides):
    event = {
        "event_id": mint_event_id(),
        "ts": "2026-09-22T00:00:00Z",
        "tool_name": "telegram_get_messages",
        "principal_ref": "prn_" + "a" * 26,
        "client_ref": "tcl_" + "b" * 26,
        "account_ref": "tga_" + "c" * 26,
        "peer_ref": None,
        "project_ref": None,
        "project_count": 1,
        "policy_epoch": 7,
        "result_count": 3,
        "duration_ms": 42,
        "telegram_rpc_count": 0,
        "status": "ok",
        "error_code": None,
        "disclosure_ref": None,
    }
    event.update(overrides)
    return event


def test_event_id_is_the_frozen_evt_shape():
    _CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    ref = mint_event_id()
    assert ref.startswith("evt_")
    assert len(ref) == 30
    body = ref[4:]
    assert len(body) == 26
    # Every character must be in the Crockford alphabet -- which excludes
    # I, L, O and U. A weaker assertion ("contains a digit") passes for any
    # string and proves nothing.
    assert set(body) <= set(_CROCKFORD)
    assert mint_event_id() != mint_event_id()


def test_genesis_is_bound_to_the_epoch():
    assert genesis_mac(1) != genesis_mac(2)
    assert len(genesis_mac(1)) == 64


def test_first_event_uses_genesis_and_sequence_one(conn):
    appended = _append(conn, _event())
    assert appended["chain_seq"] == 1
    assert appended["chain_epoch"] == 1
    assert appended["prev_event_mac"] == genesis_mac(1)


def test_sequence_is_monotonic_and_links(conn):
    first = _append(conn, _event())
    second = _append(conn, _event())
    assert second["chain_seq"] == 2
    assert second["prev_event_mac"] == first["event_mac"]


def test_head_reports_the_last_event(conn):
    _append(conn, _event())
    last = _append(conn, _event())
    assert head(conn)["event_mac"] == last["event_mac"]


def test_verify_passes_on_an_untouched_chain(conn):
    for _ in range(3):
        _append(conn, _event())
    verify_chain(conn, _KEY)


def test_editing_a_row_breaks_verification(conn):
    _append(conn, _event())
    _append(conn, _event())
    conn.execute("UPDATE audit_events SET result_count = 99 WHERE chain_seq = 1")
    conn.commit()
    with pytest.raises(ChainError):
        verify_chain(conn, _KEY)


def test_deleting_the_tail_breaks_verification(conn):
    _append(conn, _event())
    _append(conn, _event())
    conn.execute("DELETE FROM audit_events WHERE chain_seq = 2")
    conn.commit()
    # Sequence continuity holds, but the head no longer matches what the
    # anchor recorded. Task 3 covers that; here the chain alone still verifies.
    verify_chain(conn, _KEY)


def test_a_wrong_key_fails_verification(conn):
    _append(conn, _event())
    with pytest.raises(ChainError):
        verify_chain(conn, bytes(32))


def test_administrative_events_use_the_closed_dotted_vocabulary(conn):
    assert "admin.repair_anchor" in ADMIN_EVENTS
    appended = _append(conn, _event(tool_name="admin.repair_anchor", status="ok"))
    assert appended["chain_seq"] == 1
    with pytest.raises(ChainError):
        _append(conn, _event(tool_name="admin.something_new"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_audit_chain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.audit'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/audit/__init__.py`:

```python
"""Tamper-evident audit chain and its external head anchor."""
```

Create `src/telegram_mcp/disclosure/audit/chain.py`:

```python
"""MAC-linked audit chain (frozen spec §26.5; design §6.1, §6.2).

Strictly linear, one event per sequence number, appended under
``BEGIN IMMEDIATE`` so concurrent completions cannot fork the chain.

``event_id`` is inside the MAC input, so its format is part of the chain
and is frozen here rather than left to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "ADMIN_EVENTS",
    "EVENT_DOMAIN",
    "GENESIS_DOMAIN",
    "ChainError",
    "append_event",
    "immediate_transaction",
    "require_immediate_transaction",
    "event_mac",
    "genesis_mac",
    "head",
    "mint_event_id",
    "verify_chain",
]

EVENT_DOMAIN = b"telegram-mcp-audit-v1"
GENESIS_DOMAIN = b"telegram-mcp-audit-genesis-v1"

# Non-tool chain events. audit_events.tool_name is NOT NULL and §6.5 sends
# administrative and security events through the same barrier, so they need
# values. The dotted prefix cannot collide with the ten tool names.
ADMIN_EVENTS: tuple[str, ...] = (
    "admin.lock",
    "admin.unlock",
    "admin.key_rotation",
    "admin.repair_anchor",
    "admin.policy_import",
)

_TOOLS = (
    "telegram_status",
    "telegram_list_projects",
    "telegram_resolve_project",
    "telegram_list_chats",
    "telegram_resolve_peer",
    "telegram_get_unread",
    "telegram_get_messages",
    "telegram_get_context",
    "telegram_search_messages",
    "telegram_cross_project_search",
)
_ALLOWED_TOOL_NAMES = frozenset(_TOOLS) | frozenset(ADMIN_EVENTS)

# Appendix C illustrates evt_01J...: Crockford base32, uppercase, 26 chars.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_EVENT_COLUMNS = (
    "event_id", "ts", "tool_name", "principal_ref", "client_ref", "account_ref",
    "peer_ref", "project_ref", "project_count", "policy_epoch", "result_count",
    "duration_ms", "telegram_rpc_count", "status", "error_code", "disclosure_ref",
)


class ChainError(Exception):
    """The chain is inconsistent, or an event is out of contract."""


def require_immediate_transaction(conn: sqlite3.Connection) -> None:
    """Refuse to append outside a caller-owned write transaction."""
    if not conn.in_transaction:
        raise ChainError("append_event requires an open BEGIN IMMEDIATE transaction")


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """The transaction the coordinator's step 11 runs inside."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _base32_crockford(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_CROCKFORD[remainder])
    return "".join(reversed(chars))


def mint_event_id() -> str:
    """``evt_`` plus a 26-character Crockford base32 ULID."""
    timestamp = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return "evt_" + _base32_crockford(timestamp, 10) + _base32_crockford(randomness, 16)


def genesis_mac(chain_epoch: int) -> str:
    """The explicit genesis value for ``chain_seq = 1``, bound to the epoch."""
    payload = GENESIS_DOMAIN + chain_epoch.to_bytes(8, "big")
    return hashlib.sha256(payload).hexdigest()


def event_mac(
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    prev_event_mac: str,
    event: Mapping[str, Any],
) -> str:
    """HMAC over the domain, coordinates, previous link and canonical event."""
    message = (
        EVENT_DOMAIN
        + chain_epoch.to_bytes(8, "big")
        + chain_seq.to_bytes(8, "big")
        + bytes.fromhex(prev_event_mac)
        + jcs_dumps({k: event[k] for k in _EVENT_COLUMNS})
    )
    return hmac.new(chain_key, message, hashlib.sha256).hexdigest()


def head(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The last appended event, or ``None`` for an empty chain."""
    row = conn.execute(
        "SELECT chain_epoch, chain_seq, event_id, event_mac FROM audit_events"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("chain_epoch", "chain_seq", "event_id", "event_mac"), row, strict=True))


def append_event(
    conn: sqlite3.Connection, chain_key: bytes, event: Mapping[str, Any]
) -> dict[str, Any]:
    """Append one event. **The caller owns the transaction.**

    This function does NOT open or commit a transaction, and that is
    load-bearing rather than a style choice. §23A.3 requires the disclosure
    commit to be **one** transaction containing the exposure-ledger rows,
    the receipt row and exactly one audit append. If this function opened its
    own ``BEGIN IMMEDIATE`` the coordinator could not wrap it — SQLite raises
    ``cannot start a transaction within a transaction`` — and the only way
    past that error would be to drop the coordinator's transaction, silently
    turning one atomic commit into three and destroying the crash model the
    whole design rests on.

    Callers must already hold an ``BEGIN IMMEDIATE`` transaction, so the head
    read and the insert are serialised against other writers and the chain
    cannot fork. ``require_immediate_transaction`` enforces that rather than
    trusting it.
    """
    require_immediate_transaction(conn)
    if event.get("tool_name") not in _ALLOWED_TOOL_NAMES:
        raise ChainError("event tool_name is not in the closed vocabulary")
    missing = [c for c in _EVENT_COLUMNS if c not in event]
    if missing:
        raise ChainError("event is missing required columns")

    current = head(conn)
    if current is None:
        chain_epoch, chain_seq = 1, 1
        prev = genesis_mac(chain_epoch)
    else:
        chain_epoch = current["chain_epoch"]
        chain_seq = current["chain_seq"] + 1
        prev = current["event_mac"]

    mac = event_mac(
        chain_key,
        chain_epoch=chain_epoch,
        chain_seq=chain_seq,
        prev_event_mac=prev,
        event=event,
    )
    columns = ", ".join((*_EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac"))
    marks = ", ".join("?" * (len(_EVENT_COLUMNS) + 4))
    conn.execute(
        f"INSERT INTO audit_events ({columns}) VALUES ({marks})",
        (*(event[c] for c in _EVENT_COLUMNS), chain_epoch, chain_seq, prev, mac),
    )

    return {
        "chain_epoch": chain_epoch,
        "chain_seq": chain_seq,
        "event_id": event["event_id"],
        "prev_event_mac": prev,
        "event_mac": mac,
    }


def verify_chain(conn: sqlite3.Connection, chain_key: bytes) -> None:
    """Recompute every retained link. Raises ``ChainError`` on any mismatch."""
    columns = ", ".join((*_EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac"))
    rows = conn.execute(
        f"SELECT {columns} FROM audit_events ORDER BY chain_epoch, chain_seq"
    ).fetchall()

    expected_prev: str | None = None
    expected_seq: int | None = None
    for row in rows:
        record = dict(zip((*_EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac"), row, strict=True))
        if expected_seq is None:
            expected_prev = genesis_mac(record["chain_epoch"])
            expected_seq = 1
        if record["chain_seq"] != expected_seq:
            raise ChainError("chain sequence is not continuous")
        if record["prev_event_mac"] != expected_prev:
            raise ChainError("chain link does not match the previous event")
        recomputed = event_mac(
            chain_key,
            chain_epoch=record["chain_epoch"],
            chain_seq=record["chain_seq"],
            prev_event_mac=record["prev_event_mac"],
            event=record,
        )
        if not hmac.compare_digest(recomputed, record["event_mac"]):
            raise ChainError("event MAC does not verify")
        expected_prev = record["event_mac"]
        expected_seq = record["chain_seq"] + 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_audit_chain.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Prove the chain cannot fork under concurrency**

Add to `tests/unit/test_audit_chain.py`:

```python
def test_concurrent_appends_never_fork(tmp_path):
    import threading

    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    migrate(open_db(tmp_path / "meta.db"))

    def appender():
        from telegram_mcp.disclosure.audit.chain import immediate_transaction

        own = open_db(tmp_path / "meta.db")
        for _ in range(10):
            try:
                with immediate_transaction(own):
                    append_event(own, _KEY, _event())
            except Exception:  # noqa: BLE001 -- contention is expected; forks are not
                pass

    threads = [threading.Thread(target=appender) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check = open_db(tmp_path / "meta.db")
    seqs = [r[0] for r in check.execute("SELECT chain_seq FROM audit_events ORDER BY chain_seq")]
    assert seqs == list(range(1, len(seqs) + 1))
    verify_chain(check, _KEY)
```

Run: `uv run pytest tests/unit/test_audit_chain.py::test_concurrent_appends_never_fork -v`
Expected: PASS, with a contiguous sequence and a verifying chain.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/disclosure/audit/ tests/unit/test_audit_chain.py
git commit -m "feat: add the MAC-linked audit chain"
```

---

### Task 3: The external anchor

**Files:**
- Create: `src/telegram_mcp/disclosure/audit/anchor.py`
- Test: `tests/unit/test_audit_anchor.py`

**Interfaces:**
- Produces:
  - `ANCHOR_DOMAIN = b"telegram-mcp-anchor-v1"`, `ANCHOR_VERSION = 1`
  - `CLEAN, ANCHOR_PENDING, RECOVERY_REQUIRED, DEGRADED, FAIL_CLOSED: str`
  - `write_anchor(path, chain_key, *, chain_epoch, chain_seq, event_id, event_mac, now) -> None`
  - `read_anchor(path, chain_key) -> dict[str, Any]`
  - `derive_integrity(conn, chain_key, path) -> str`
  - `AnchorError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_audit_anchor.py`:

```python
"""External head anchor (design §6.3, §6.7; frozen spec §12.2)."""

import json
import os

import pytest

from telegram_mcp.disclosure.audit.anchor import (
    ANCHOR_VERSION,
    CLEAN,
    FAIL_CLOSED,
    RECOVERY_REQUIRED,
    AnchorError,
    derive_integrity,
    read_anchor,
    write_anchor,
)
from telegram_mcp.disclosure.audit.chain import append_event, head, mint_event_id
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate

_KEY = bytes(range(32))

def _append(conn, event):
    """Every append runs inside a caller-owned transaction (design §6.5)."""
    from telegram_mcp.disclosure.audit.chain import immediate_transaction

    with immediate_transaction(conn):
        return append_event(conn, _KEY, event)



@pytest.fixture
def anchor_dir(tmp_path):
    directory = tmp_path / "anchor"
    directory.mkdir(mode=0o700)
    return directory


def _write(path, **overrides):
    fields = {
        "chain_epoch": 1, "chain_seq": 1,
        "event_id": "evt_" + "0" * 26, "event_mac": "a" * 64,
        "now": "2026-09-22T00:00:00Z",
    }
    fields.update(overrides)
    write_anchor(path, _KEY, **fields)


def test_anchor_round_trips_with_the_frozen_fields(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    anchor = read_anchor(path, _KEY)
    assert anchor["version"] == ANCHOR_VERSION
    assert set(anchor) == {"version", "chain_epoch", "chain_seq", "event_id", "event_mac", "updated_at", "anchor_mac"}


def test_anchor_file_is_0600(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_a_tampered_anchor_is_refused(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    body = json.loads(path.read_text())
    body["chain_seq"] = 99
    path.write_text(json.dumps(body))
    with pytest.raises(AnchorError):
        read_anchor(path, _KEY)


def test_a_symlinked_anchor_is_refused(anchor_dir, tmp_path):
    real = tmp_path / "elsewhere.json"
    _write(real)
    link = anchor_dir / "anchor.json"
    link.symlink_to(real)
    with pytest.raises(AnchorError):
        read_anchor(link, _KEY)


def test_a_world_readable_anchor_is_refused(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    os.chmod(path, 0o644)
    with pytest.raises(AnchorError):
        read_anchor(path, _KEY)


def test_an_unknown_version_is_refused(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    body = json.loads(path.read_text())
    body["version"] = 99
    path.write_text(json.dumps(body))
    with pytest.raises(AnchorError):
        read_anchor(path, _KEY)


def test_no_temp_file_survives_a_successful_write(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    _write(path, chain_seq=2)
    assert [p.name for p in anchor_dir.iterdir()] == ["anchor.json"]


# --- the integrity table (design §6.7) --------------------------------------


def _chain(tmp_path):
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    return conn


def _event():
    return {
        "event_id": mint_event_id(), "ts": "2026-09-22T00:00:00Z",
        "tool_name": "telegram_get_messages", "principal_ref": "prn_a", "client_ref": "tcl_a",
        "account_ref": "tga_a", "peer_ref": None, "project_ref": None, "project_count": 1,
        "policy_epoch": 1, "result_count": 1, "duration_ms": 1, "telegram_rpc_count": 0,
        "status": "ok", "error_code": None, "disclosure_ref": None,
    }


def test_anchor_matching_the_head_is_clean(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    appended = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: appended[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    assert derive_integrity(conn, _KEY, path) == CLEAN


def test_head_one_ahead_is_recovery_required(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    first = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: first[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    _append(conn, _event())  # crashed before refreshing the anchor
    assert derive_integrity(conn, _KEY, path) == RECOVERY_REQUIRED


def test_head_two_ahead_fails_closed(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    first = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: first[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    _append(conn, _event())
    _append(conn, _event())
    assert derive_integrity(conn, _KEY, path) == FAIL_CLOSED


def test_anchor_ahead_of_the_head_fails_closed(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    appended = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: appended[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    conn.execute("DELETE FROM audit_events")  # DB-only truncation
    conn.commit()
    assert derive_integrity(conn, _KEY, path) == FAIL_CLOSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_audit_anchor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.audit.anchor'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/audit/anchor.py`:

```python
"""External audit-head anchor (frozen spec §12.2; design §6.3, §6.7).

A daemon-owned ``0600`` file in a ``0700`` non-database directory — the
spec's reviewed fallback. Its format is frozen because a file two
implementations write differently is not an anchor.

The refresh is durable or it did not happen: write a temp file, fsync it,
rename it over the anchor, then fsync the directory. A refresh that cannot
complete every step reports failure and never reports success.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.audit.chain import ChainError, head, verify_chain

__all__ = [
    "ANCHOR_DOMAIN",
    "ANCHOR_PENDING",
    "ANCHOR_VERSION",
    "CLEAN",
    "DEGRADED",
    "FAIL_CLOSED",
    "RECOVERY_REQUIRED",
    "AnchorError",
    "derive_integrity",
    "read_anchor",
    "write_anchor",
]

ANCHOR_DOMAIN = b"telegram-mcp-anchor-v1"
ANCHOR_VERSION = 1

CLEAN = "CLEAN"
ANCHOR_PENDING = "ANCHOR_PENDING"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
DEGRADED = "DEGRADED"
FAIL_CLOSED = "FAIL_CLOSED"


class AnchorError(Exception):
    """The anchor is unreadable, unauthenticated or out of contract."""


def _anchor_mac(chain_key: bytes, body: dict[str, Any]) -> str:
    without = {k: v for k, v in body.items() if k != "anchor_mac"}
    return hmac.new(chain_key, ANCHOR_DOMAIN + jcs_dumps(without), hashlib.sha256).hexdigest()


def write_anchor(
    path: str | Path,
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    event_id: str,
    event_mac: str,
    now: str,
) -> None:
    """Durable refresh: temp write, fsync, rename, fsync the directory."""
    target = Path(path)
    body: dict[str, Any] = {
        "version": ANCHOR_VERSION,
        "chain_epoch": chain_epoch,
        "chain_seq": chain_seq,
        "event_id": event_id,
        "event_mac": event_mac,
        "updated_at": now,
    }
    body["anchor_mac"] = _anchor_mac(chain_key, body)

    temp = target.with_name(f"{target.name}.tmp.{secrets.token_hex(8)}")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(body).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def read_anchor(path: str | Path, chain_key: bytes) -> dict[str, Any]:
    """Read and authenticate. Every check below fails closed, never warns."""
    target = Path(path)
    try:
        info = os.lstat(target)
    except OSError as exc:
        raise AnchorError("anchor is unreadable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AnchorError("anchor must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise AnchorError("anchor must be a regular file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise AnchorError("anchor permissions must be 0600")
    if info.st_uid != os.geteuid():
        raise AnchorError("anchor must be owned by the runtime account")

    parent = os.stat(target.parent)
    if stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.geteuid():
        raise AnchorError("anchor directory must be 0700 and owned by the runtime account")

    try:
        body = json.loads(target.read_text())
    except ValueError as exc:
        raise AnchorError("anchor is not valid JSON") from exc
    if body.get("version") != ANCHOR_VERSION:
        raise AnchorError("unsupported anchor version")
    if not hmac.compare_digest(_anchor_mac(chain_key, body), str(body.get("anchor_mac", ""))):
        raise AnchorError("anchor MAC does not verify")
    return body


def derive_integrity(conn: sqlite3.Connection, chain_key: bytes, path: str | Path) -> str:
    """Recompute integrity from the anchor and the chain (design §6.7).

    Equality alone never proclaims integrity: the head MAC must verify, the
    sequence must be continuous, and the retained chain must verify.
    """
    try:
        anchor = read_anchor(path, chain_key)
    except AnchorError:
        return FAIL_CLOSED

    current = head(conn)
    if current is None:
        return FAIL_CLOSED if anchor["chain_seq"] > 0 else CLEAN

    if (anchor["chain_epoch"], anchor["chain_seq"]) > (current["chain_epoch"], current["chain_seq"]):
        # The database lost committed history. Moving the pointer would be
        # laundering the evidence, so this is fatal, not repairable.
        return FAIL_CLOSED

    try:
        verify_chain(conn, chain_key)
    except ChainError:
        return FAIL_CLOSED

    if anchor["chain_epoch"] != current["chain_epoch"]:
        return FAIL_CLOSED

    gap = current["chain_seq"] - anchor["chain_seq"]
    if gap == 0:
        return CLEAN if anchor["event_mac"] == current["event_mac"] else FAIL_CLOSED
    if gap == 1:
        return RECOVERY_REQUIRED
    return FAIL_CLOSED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_audit_anchor.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/audit/anchor.py tests/unit/test_audit_anchor.py
git commit -m "feat: add the external audit-head anchor"
```

---

### Task 4: Checkpoints and `audit verify`

**Files:**
- Modify: `src/telegram_mcp/disclosure/audit/chain.py`
- Test: `tests/unit/test_audit_chain.py`

**Interfaces:**
- Produces:
  - `write_checkpoint(conn, checkpoint_key, *, now) -> dict[str, Any]`
  - `verify_checkpoints(conn, checkpoint_public) -> None`
  - `checkpoint_due(conn, *, events_since: int, seconds_since: int) -> bool`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_audit_chain.py`:

```python
def test_checkpoint_signs_the_current_head(conn):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.disclosure.audit.chain import verify_checkpoints, write_checkpoint

    seed = bytes(range(32))
    appended = _append(conn, _event())
    checkpoint = write_checkpoint(conn, seed, now="2026-09-22T00:00:00Z")

    assert checkpoint["chain_seq"] == appended["chain_seq"]
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    verify_checkpoints(conn, public)


def test_a_tampered_checkpoint_fails_verification(conn):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.disclosure.audit.chain import verify_checkpoints, write_checkpoint

    seed = bytes(range(32))
    _append(conn, _event())
    write_checkpoint(conn, seed, now="2026-09-22T00:00:00Z")
    conn.execute("UPDATE audit_checkpoints SET last_event_mac = 'deadbeef'")
    conn.commit()

    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    with pytest.raises(ChainError):
        verify_checkpoints(conn, public)


def test_cadence_respects_the_spec_bound(conn):
    from telegram_mcp.disclosure.audit.chain import checkpoint_due

    # §26.5: at least every 500 events or 60 minutes, whichever comes first.
    assert checkpoint_due(conn, events_since=500, seconds_since=0)
    assert checkpoint_due(conn, events_since=0, seconds_since=3_600)
    assert not checkpoint_due(conn, events_since=99, seconds_since=60)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_audit_chain.py -k checkpoint -v`
Expected: FAIL with `ImportError: cannot import name 'write_checkpoint'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/telegram_mcp/disclosure/audit/chain.py`:

```python
CHECKPOINT_DOMAIN = b"telegram-mcp-checkpoint-v1"


def _checkpoint_message(row: Mapping[str, Any]) -> bytes:
    return CHECKPOINT_DOMAIN + jcs_dumps(
        {
            "chain_epoch": row["chain_epoch"],
            "chain_seq": row["chain_seq"],
            "last_event_id": row["last_event_id"],
            "last_event_mac": row["last_event_mac"],
            "created_at": row["created_at"],
        }
    )


def write_checkpoint(
    conn: sqlite3.Connection, checkpoint_key: bytes, *, now: str
) -> dict[str, Any]:
    """Sign the current head with the dedicated audit-checkpoint key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.opaque import mint_opaque_ref

    current = head(conn)
    if current is None:
        raise ChainError("cannot checkpoint an empty chain")

    row = {
        "checkpoint_ref": mint_opaque_ref("tgl_"),
        "chain_epoch": current["chain_epoch"],
        "chain_seq": current["chain_seq"],
        "last_event_id": current["event_id"],
        "last_event_mac": current["event_mac"],
        "created_at": now,
    }
    signature = Ed25519PrivateKey.from_private_bytes(checkpoint_key).sign(_checkpoint_message(row))
    signing_key_id = "ed25519:sha256:" + hashlib.sha256(
        Ed25519PrivateKey.from_private_bytes(checkpoint_key).public_key().public_bytes_raw()
    ).hexdigest()

    conn.execute(
        "INSERT INTO audit_checkpoints (checkpoint_ref, chain_epoch, chain_seq, last_event_id,"
        " last_event_mac, created_at, signing_key_id, signature)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["checkpoint_ref"], row["chain_epoch"], row["chain_seq"], row["last_event_id"],
            row["last_event_mac"], row["created_at"], signing_key_id, signature.hex(),
        ),
    )
    conn.commit()
    return row


def verify_checkpoints(conn: sqlite3.Connection, checkpoint_public: bytes) -> None:
    """Verify every retained checkpoint signature."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public = Ed25519PublicKey.from_public_bytes(checkpoint_public)
    rows = conn.execute(
        "SELECT chain_epoch, chain_seq, last_event_id, last_event_mac, created_at, signature"
        " FROM audit_checkpoints ORDER BY chain_epoch, chain_seq"
    ).fetchall()
    for row in rows:
        record = dict(
            zip(
                ("chain_epoch", "chain_seq", "last_event_id", "last_event_mac", "created_at", "signature"),
                row,
                strict=True,
            )
        )
        try:
            public.verify(bytes.fromhex(record["signature"]), _checkpoint_message(record))
        except (InvalidSignature, ValueError) as exc:
            raise ChainError("checkpoint signature does not verify") from exc


def checkpoint_due(conn: sqlite3.Connection, *, events_since: int, seconds_since: int) -> bool:
    """§26.5 cadence: at least every 500 events or 60 minutes, whichever first.

    The settings maxima are pinned to that bound, so a configured cadence can
    only be tighter than the MUST, never looser.
    """
    from telegram_mcp.storage.settings import get_setting

    return events_since >= get_setting(conn, "audit.checkpoint_cadence_events") or (
        seconds_since >= get_setting(conn, "audit.checkpoint_cadence_seconds")
    )
```

Extend `__all__` with `"CHECKPOINT_DOMAIN"`, `"checkpoint_due"`, `"verify_checkpoints"`, `"write_checkpoint"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_audit_chain.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/audit/chain.py tests/unit/test_audit_chain.py
git commit -m "feat: add signed audit checkpoints and the cadence rule"
```

---

### Task 5: The coordinator and the append guard

**Files:**
- Create: `src/telegram_mcp/disclosure/coordinator.py`
- Test: `tests/integration/test_disclosure_coordinator.py`

**Interfaces:**
- Consumes: everything from Plans 3a and 3b, plus Tasks 2–4 here.
- Produces:
  - `DISCLOSURE_STEPS: tuple[str, ...]` — the twelve names, in order.
  - `RetrievalAdapter` protocol with `async def retrieve(self, *, tool_name, arguments) -> dict[str, Any]`
  - `DisclosureOutcome` — frozen dataclass, either `(released=True, data, meta, disclosure_ref)` or `(released=False, error_code, retryable)`
  - `DisclosureCoordinator(conn, *, keys, anchor_path, ledger, broker, clock)` with `async def disclose(...) -> DisclosureOutcome`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_disclosure_coordinator.py`:

```python
"""The twelve-step disclosure transaction (design §2, §6.5)."""

import pytest

from telegram_mcp.disclosure.coordinator import DISCLOSURE_STEPS


def test_the_twelve_steps_are_frozen_in_order():
    assert DISCLOSURE_STEPS == (
        "freeze_arguments",
        "snapshot_authority",
        "estimate_exposure",
        "consent_issue",
        "consent_consume",
        "reserve_budget",
        "retrieve",
        "revalidate_authority",
        "transform_egress",
        "measure_and_prepare_proof",
        "commit_disclosure",
        "refresh_anchor",
    )


def test_retrieval_never_happens_before_consent_is_consumed():
    # The security barrier: steps 1-6 must precede any adapter call.
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("consent_consume")
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("reserve_budget")


def test_the_anchor_is_the_last_step():
    assert DISCLOSURE_STEPS[-1] == "refresh_anchor"
    assert DISCLOSURE_STEPS.index("commit_disclosure") == DISCLOSURE_STEPS.index("refresh_anchor") - 1
```

Add the behavioural tests once the coordinator exists; they belong in the same file and are listed in Task 6.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_disclosure_coordinator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.coordinator'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/coordinator.py`. The module docstring and the step tuple come first; the class follows.

```python
"""The disclosure coordinator (design §1.2, §2, §6.5).

The coordinator **sequences**; it does not decide. Authority decisions stay
in ``authority/``, consent in ``consent/``, budgets in ``budget.py``,
integrity in ``audit/``. A second policy engine here would be the failure
this design exists to prevent.

It is also the only thing in the system able to release a sensitive payload.
``DisclosureOutcome`` is either a released payload carrying its committed
receipt, or a refusal — there is no third shape, so a caller cannot obtain
data without a receipt.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "DISCLOSURE_STEPS",
    "DisclosureCoordinator",
    "DisclosureOutcome",
    "RetrievalAdapter",
]

# Frozen as a declaration the tests assert against. Production calls typed
# functions explicitly rather than dispatching over this tuple: a security
# protocol must not be a dynamically dispatchable plugin chain.
DISCLOSURE_STEPS: tuple[str, ...] = (
    "freeze_arguments",
    "snapshot_authority",
    "estimate_exposure",
    "consent_issue",
    "consent_consume",
    "reserve_budget",
    "retrieve",
    "revalidate_authority",
    "transform_egress",
    "measure_and_prepare_proof",
    "commit_disclosure",
    "refresh_anchor",
)

# A single process-wide guard, acquired BEFORE step 11 and held across step
# 12. Marking the chain ANCHOR_PENDING only after committing would leave a
# window in which a second caller commits and the chain goes two ahead.
_APPEND_GUARD = threading.Lock()


class RetrievalAdapter(Protocol):
    """The Phase-4 seam. Faked in Phase 3, exactly as Phase 2 faked its own."""

    async def retrieve(
        self, *, tool_name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DisclosureOutcome:
    """Either a released payload with its receipt, or a refusal. Never both."""

    released: bool
    data: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    disclosure_ref: str | None = None
    error_code: str | None = None
    retryable: bool = False
```

Then the coordinator itself. Each step is its own method so a crash can be
injected at any one of the twelve by name:

```python
class DisclosureCoordinator:
    """Runs the twelve steps. Sequences; never decides."""

    def __init__(
        self,
        conn,
        *,
        chain_key: bytes,
        checkpoint_key: bytes,
        disclosure_seed: bytes,
        disclosure_key_id: str,
        anchor_path,
        ledger,
        authority,
        consent,
        transport=None,
        clock=time.time,
        crash_at: str | None = None,
    ) -> None:
        self._conn = conn
        self._chain_key = chain_key
        self._checkpoint_key = checkpoint_key
        self._disclosure_seed = disclosure_seed
        self._disclosure_key_id = disclosure_key_id
        self._anchor_path = anchor_path
        self._ledger = ledger
        self._authority = authority
        self._consent = consent
        self._transport = transport
        self._clock = clock
        self._crash_at = crash_at

    def _checkpoint(self, step: str) -> None:
        """Crash-injection seam. One branch, at the top of every step, and it
        is not a test-only flag inside a real path: ``crash_at`` is a
        constructor argument that production never supplies."""
        if self._crash_at == step:
            raise RuntimeError(f"injected crash at {step}")

    async def disclose(self, *, tool_name, arguments, adapter) -> DisclosureOutcome:
        if get_setting(self._conn, "audit.integrity_degraded"):
            # Degraded refuses before retrieval, and appends nothing: while
            # the anchor cannot advance, a further append would put the chain
            # more than one ahead and make it unrecoverable at startup.
            return DisclosureOutcome(
                released=False, error_code="AUDIT_INTEGRITY_UNAVAILABLE", retryable=False
            )

        reservation = None
        try:
            # ---- steps 1-6: nothing has been retrieved ----------------------
            self._checkpoint("freeze_arguments")
            request = self._authority.freeze_arguments(tool_name, arguments)

            self._checkpoint("snapshot_authority")
            snapshot = self._authority.snapshot(tool_name, request)

            self._checkpoint("estimate_exposure")
            worst_case = self._authority.worst_case_buckets(tool_name, snapshot)
            decision, projected = self._ledger.consult(worst_case)
            if decision == "refuse":
                return DisclosureOutcome(
                    released=False, error_code="EXPOSURE_BUDGET_EXCEEDED", retryable=True
                )

            for attempt in range(2):  # exactly one automatic restart (design §5.5)
                self._checkpoint("consent_issue")
                challenge = self._consent.issue(
                    tool_name=tool_name, snapshot=snapshot, projected=projected, tier=decision
                )

                self._checkpoint("consent_consume")
                approval = await self._consent.consume(challenge)
                if approval is None:
                    return DisclosureOutcome(
                        released=False, error_code="CONSENT_DENIED", retryable=False
                    )

                self._checkpoint("reserve_budget")
                try:
                    reservation = self._ledger.reserve(
                        client_id=snapshot.client_id,
                        security_epoch=snapshot.security_epoch,
                        project_scope_digest=snapshot.project_scope_digest,
                        consent_challenge_digest=approval.challenge_sha256,
                        request_nonce=request.nonce,
                        worst_case=worst_case,
                        ttl_seconds=60,
                    )
                except BudgetError:
                    return DisclosureOutcome(
                        released=False, error_code="EXPOSURE_BUDGET_EXCEEDED", retryable=True
                    )
                if self._consent.snapshot_matches(approval, worst_case):
                    break
                self._ledger.release(reservation.reservation_ref)
                reservation = None
            else:
                # Second divergence: stop an agent spinning through prompts.
                return DisclosureOutcome(
                    released=False, error_code="CONSENT_UNAVAILABLE", retryable=False
                )

            # ==== SECURITY BARRIER ==========================================
            self._checkpoint("retrieve")
            raw = await adapter.retrieve(tool_name=tool_name, arguments=arguments)

            self._checkpoint("revalidate_authority")
            moved = self._authority.revalidate(snapshot)
            if moved is not None:
                return DisclosureOutcome(released=False, error_code=moved, retryable=False)

            self._checkpoint("transform_egress")
            data = self._authority.apply_egress(raw, snapshot)

            self._checkpoint("measure_and_prepare_proof")
            prepared = self._prepare_proof(tool_name, data, snapshot, approval)
            actual = buckets_for(tool_name, data, client_id=snapshot.client_id)

            # ==== DISCLOSURE BARRIER: steps 11 and 12 under one guard =======
            with _APPEND_GUARD:
                self._checkpoint("commit_disclosure")
                try:
                    with immediate_transaction(self._conn):
                        # ONE transaction: ledger rows, receipt row, exactly
                        # one audit append (frozen spec §23A.3).
                        self._ledger.commit(
                            reservation,
                            disclosure_ref=prepared["disclosure_ref"],
                            actual=actual,
                            effective_egress_level=prepared["effective_egress_level"],
                            ts=prepared["committed_at"],
                        )
                        self._insert_receipt(prepared, snapshot)
                        appended = append_event(
                            self._conn, self._chain_key, self._audit_event(prepared, snapshot)
                        )
                except BudgetError:
                    # actual > reserved: the estimator is wrong, and that is
                    # not a licence to charge more.
                    self._ledger.release(reservation.reservation_ref)
                    return DisclosureOutcome(
                        released=False, error_code="PROOF_GENERATION_FAILED", retryable=True
                    )
                reservation = None  # the commit consumed it

                self._checkpoint("refresh_anchor")
                try:
                    write_anchor(
                        self._anchor_path,
                        self._chain_key,
                        now=prepared["committed_at"],
                        **{k: appended[k] for k in
                           ("chain_epoch", "chain_seq", "event_id", "event_mac")},
                    )
                except (AnchorError, OSError, RuntimeError):
                    self._latch_degraded(prepared["disclosure_ref"], "anchor_refresh_failure")
                    return DisclosureOutcome(
                        released=False, error_code="AUDIT_INTEGRITY_UNAVAILABLE", retryable=False
                    )

            # ==== only now may a byte cross the boundary ====================
            meta = self._build_meta(prepared)
            if self._transport is not None:
                self._transport.write(jcs_dumps(data))
            return DisclosureOutcome(
                released=True, data=data, meta=meta,
                disclosure_ref=prepared["disclosure_ref"],
            )
        finally:
            # Conservative cleanup: a reservation freed against a commit that
            # may have happened is a hole; a stale one is an annoyance.
            if reservation is not None:
                self._ledger.release(reservation.reservation_ref)

    def _latch_degraded(self, disclosure_ref: str, reason: str) -> None:
        set_setting(self._conn, "audit.integrity_degraded", 1)
        set_setting(self._conn, "audit.degraded_disclosure_ref", disclosure_ref)
        set_setting(self._conn, "audit.degraded_reason", reason)
```

`_prepare_proof`, `_insert_receipt`, `_audit_event` and `_build_meta` are
straight assembly over Plan 3a: `_prepare_proof` mints the `tdr_` ref, calls
`records_disclosed`, `bytes_disclosed`, `effective_egress_level`,
`provenance_digest`, `coverage_digest`, then `build_proof_payload` and
`sign_payload`; `_insert_receipt` writes those columns into
`disclosure_receipts`; `_audit_event` fills the sixteen `_EVENT_COLUMNS`;
`_build_meta` assembles the frozen `meta` envelope with the `disclosure` and
`coverage` objects.

Release the guard on every path — the `with` statement does that, and a guard
leaked by an exception would convert one anchor failure into a permanently
frozen daemon.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_disclosure_coordinator.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/coordinator.py tests/integration/test_disclosure_coordinator.py
git commit -m "feat: add the disclosure coordinator and the append guard"
```

---

### Task 6: The crash model and the no-escape suite

**Files:**
- Modify: `tests/integration/test_disclosure_coordinator.py`
- Create: `tests/security/test_no_uncommitted_escape.py`

- [ ] **Step 1: Write the crash-injection tests**

Append to `tests/integration/test_disclosure_coordinator.py` one test per crash point, asserting the design's crash-model table:

```python
@pytest.mark.parametrize("crash_at", DISCLOSURE_STEPS[:10])
async def test_crashing_before_commit_leaves_nothing_durable(crash_at, coordinator_factory):
    coordinator, conn, adapter = coordinator_factory(crash_at=crash_at)
    outcome = await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)

    assert not outcome.released
    assert conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM exposure_ledger").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0


async def test_crashing_between_commit_and_anchor_charges_and_withholds(coordinator_factory):
    coordinator, conn, adapter = coordinator_factory(crash_at="refresh_anchor")
    outcome = await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)

    assert not outcome.released
    assert outcome.error_code == "AUDIT_INTEGRITY_UNAVAILABLE"
    # Accounted, and deliberately not refunded: a payload that may have been
    # produced is conservatively treated as charged.
    assert conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM exposure_ledger").fetchone()[0] >= 1
    assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
    assert get_setting(conn, "audit.integrity_degraded") == 1
    assert get_setting(conn, "audit.degraded_reason") == "anchor_refresh_failure"
    assert get_setting(conn, "audit.degraded_disclosure_ref").startswith("tdr_")


async def test_degraded_refuses_the_nine_sensitive_tools_and_appends_nothing(coordinator_factory):
    coordinator, conn, adapter = coordinator_factory(crash_at="refresh_anchor")
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    before = conn.execute("SELECT count(*) FROM audit_events").fetchone()[0]

    outcome = await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)

    assert outcome.error_code == "AUDIT_INTEGRITY_UNAVAILABLE"
    # While degraded the anchor cannot advance, so nothing may append: a
    # chain that grows here becomes unrecoverable at startup. The refusal is
    # therefore NOT audited, and the degraded record is the evidence.
    assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == before
```

Write the fixture in the same file. Four suites depend on it, so it is real
code, not a description:

```python
@pytest.fixture
def coordinator_factory(tmp_path):
    """Build a coordinator over a real database, ledger, chain and anchor."""

    def build(*, crash_at=None, transport=None, records=None, on_anchor=None):
        from telegram_mcp.disclosure.budget import BudgetLedger
        from telegram_mcp.disclosure.coordinator import DisclosureCoordinator
        from telegram_mcp.keys.store import load_key, provision_missing
        from telegram_mcp.storage.db import open_db
        from telegram_mcp.storage.migrations import migrate

        provision_missing(tmp_path / "keys", phases=(2, 3))
        conn = open_db(tmp_path / "meta.db")
        migrate(conn)
        seed_authority_rows(conn)  # one account, principal, client, project

        anchor_dir = tmp_path / "anchor"
        anchor_dir.mkdir(mode=0o700, exist_ok=True)

        class FakeAdapter:
            async def retrieve(self, *, tool_name, arguments):
                return {
                    "project": {"project_ref": PROJECT_REF},
                    "messages": records
                    or [
                        {"message_ref": "tgm_" + c * 26,
                         "origin_project_refs": [PROJECT_REF],
                         "text": "hello", "text_truncated": False}
                        for c in "ab"
                    ],
                }

        coordinator = DisclosureCoordinator(
            conn,
            chain_key=load_key("audit-chain-key"),
            checkpoint_key=load_key("audit-checkpoint-key"),
            disclosure_seed=load_key("disclosure-key"),
            disclosure_key_id=key_id("disclosure-key"),
            anchor_path=anchor_dir / "anchor.json",
            ledger=BudgetLedger(conn),
            authority=FakeAuthority(conn),
            consent=FakeConsent(on_anchor=on_anchor),
            transport=transport,
            crash_at=crash_at,
        )
        return coordinator, conn, FakeAdapter()

    return build
```

`FakeAuthority` and `FakeConsent` are the two seams Phase 4 replaces:
`FakeAuthority` returns a fixed snapshot and never reports movement unless a
test asks it to; `FakeConsent` issues a challenge and approves it immediately.
`seed_authority_rows` inserts one account, principal, client and project so
the receipt's foreign keys and the `disclosure_receipts_tuple_consistency_insert`
trigger are satisfied — write it once in `tests/conftest.py` and import it,
because Plan 3b Task 5 needs the same rows.

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/integration/test_disclosure_coordinator.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 3: Write the no-escape test**

Create `tests/security/test_no_uncommitted_escape.py`:

```python
"""No sensitive byte crosses the boundary before the anchor (design §2)."""


async def test_a_recording_transport_sees_nothing_before_the_anchor(coordinator_factory):
    writes: list[bytes] = []

    class RecordingTransport:
        def write(self, payload: bytes) -> None:
            writes.append(payload)

    coordinator, _conn, adapter = coordinator_factory(crash_at="refresh_anchor", transport=RecordingTransport())
    outcome = await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)

    assert not outcome.released
    # The anchor failed, so not one byte may have reached the transport.
    assert writes == []


async def test_a_successful_call_writes_only_after_the_anchor(coordinator_factory):
    order: list[str] = []

    class OrderingTransport:
        def write(self, payload: bytes) -> None:
            order.append("transport")

    coordinator, _conn, adapter = coordinator_factory(transport=OrderingTransport(), on_anchor=lambda: order.append("anchor"))
    outcome = await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)

    assert outcome.released
    assert order == ["anchor", "transport"]
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/security/test_no_uncommitted_escape.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_disclosure_coordinator.py tests/security/test_no_uncommitted_escape.py
git commit -m "test: pin the crash model and the no-escape rule"
```

---

### Task 7: The content-leak sweep

Gate O's first privacy MUST: message and search content never appears in receipts, the exposure ledger, the audit chain or checkpoints. A whole-store scan, so a column added later cannot escape it.

**Files:**
- Create: `tests/security/test_no_content_in_stores.py`

- [ ] **Step 1: Write the test**

```python
"""Gate O: no content in receipts, ledger, chain, checkpoints, settings or logs."""

import logging

# Distinctive markers: if any of these ever appears in a store, the sweep
# names the table and column rather than just failing.
_MARKERS = (
    "ZQXJV-MESSAGE-BODY",
    "ZQXJV-SEARCH-QUERY",
    "ZQXJV-USERNAME",
    "+61400000000",
)


def _scan(conn) -> list[str]:
    hits: list[str] = []
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    ]
    for table in tables:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for row in conn.execute(f"SELECT * FROM {table}").fetchall():
            for column, value in zip(columns, row, strict=True):
                if isinstance(value, str) and any(m in value for m in _MARKERS):
                    hits.append(f"{table}.{column}")
    return hits


async def test_no_marker_reaches_any_store(coordinator_factory, caplog):
    coordinator, conn, adapter = coordinator_factory(
        records=[
            {
                "message_ref": "tgm_" + "a" * 26,
                "origin_project_refs": ["tpr_" + "a" * 26],
                "text": f"{_MARKERS[0]} from {_MARKERS[2]} at {_MARKERS[3]}",
            }
        ]
    )
    with caplog.at_level(logging.DEBUG):
        outcome = await coordinator.disclose(
            tool_name="telegram_search_messages", arguments={"query": _MARKERS[1]}, adapter=adapter
        )

    assert outcome.released
    assert _scan(conn) == []
    assert not any(m in caplog.text for m in _MARKERS)


def test_the_sweep_would_actually_catch_a_leak(tmp_path):
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    conn_with_schema = open_db(tmp_path / "meta.db")
    migrate(conn_with_schema)
    # A sweep that cannot fail proves nothing. Plant a marker and confirm.
    conn_with_schema.execute(
        "INSERT INTO settings (key, value_json, updated_at) VALUES ('release.version', ?, 'now')",
        (f'"{_MARKERS[0]}"',),
    )
    conn_with_schema.commit()
    assert _scan(conn_with_schema) == ["settings.value_json"]
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/security/test_no_content_in_stores.py -v`
Expected: PASS, 2 tests. The second is the control: a sweep that cannot fail proves nothing.

- [ ] **Step 3: Commit**

```bash
git add tests/security/test_no_content_in_stores.py
git commit -m "test: sweep every store for Telegram content"
```

---

### Task 8: Recovery ceremony and the operator commands

**Files:**
- Modify: `src/telegram_mcp/disclosure/audit/anchor.py`, `src/telegram_mcp/ipc/admin.py`, `src/telegram_mcp/cli.py`
- Test: `tests/integration/test_audit_recovery.py`

**Interfaces:**
- Produces: `repair_anchor(conn, chain_key, checkpoint_key, path, *, now) -> None`; admin routes for `disclosure show|verify|key`, `exposure status`, `audit verify|checkpoint|repair-anchor`.

The `degraded_daemon` and `truncated_daemon` fixtures build on
`coordinator_factory`: `degraded_daemon` runs one disclosure with
`crash_at="refresh_anchor"` and returns `(conn, chain_key, checkpoint_key,
path)`; `truncated_daemon` runs one clean disclosure and then deletes every
`audit_events` row, leaving the anchor ahead of the head. Put both in
`tests/conftest.py` beside `coordinator_factory`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_audit_recovery.py`:

```python
"""The repair ceremony and its ordering (design §6.8)."""

import pytest

from telegram_mcp.disclosure.audit.anchor import CLEAN, RECOVERY_REQUIRED, derive_integrity, repair_anchor


def test_repair_restores_clean_and_audits_itself(degraded_daemon):
    conn, chain_key, checkpoint_key, path = degraded_daemon
    assert derive_integrity(conn, chain_key, path) == RECOVERY_REQUIRED

    repair_anchor(conn, chain_key, checkpoint_key, path, now="2026-09-22T01:00:00Z")

    assert derive_integrity(conn, chain_key, path) == CLEAN
    # The repair is itself a normally chained, anchored event, and the latch
    # clears only after that event is anchored.
    last = conn.execute(
        "SELECT tool_name FROM audit_events ORDER BY chain_seq DESC LIMIT 1"
    ).fetchone()
    assert last[0] == "admin.repair_anchor"
    from telegram_mcp.storage.settings import get_setting

    assert get_setting(conn, "audit.integrity_degraded") == 0
    assert get_setting(conn, "audit.degraded_disclosure_ref") == ""


def test_repair_refuses_when_the_chain_does_not_extend(truncated_daemon):
    conn, chain_key, checkpoint_key, path = truncated_daemon
    # Anchor ahead of the head: the database lost committed history. Moving
    # the pointer would launder the evidence, so repair must refuse.
    with pytest.raises(Exception):
        repair_anchor(conn, chain_key, checkpoint_key, path, now="2026-09-22T01:00:00Z")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/integration/test_audit_recovery.py -v`
Expected: FAIL with `ImportError: cannot import name 'repair_anchor'`.

- [ ] **Step 3: Implement `repair_anchor` in the six-step order**

```python
def repair_anchor(
    conn: sqlite3.Connection,
    chain_key: bytes,
    checkpoint_key: bytes,
    path: str | Path,
    *,
    now: str,
) -> None:
    """User-presence-gated recovery (design §6.8). The order is load-bearing.

    Appends are forbidden while degraded, so the repair event cannot be
    written first; and a latch cleared before that event is written would
    leave the gap unexplained in the one record built to explain gaps.
    """
    from telegram_mcp.disclosure.audit.chain import append_event, head, mint_event_id
    from telegram_mcp.storage.settings import get_setting, set_setting

    state = derive_integrity(conn, chain_key, path)
    if state != RECOVERY_REQUIRED:
        # Every FAIL CLOSED row is fatal, not repairable: an anchor ahead of
        # the head, a head more than one ahead, or a MAC mismatch is not a
        # stale pointer and is not fixed by moving the pointer.
        raise AnchorError("integrity state is not repairable")

    withheld = get_setting(conn, "audit.degraded_disclosure_ref")
    reason = get_setting(conn, "audit.degraded_reason")

    current = head(conn)                                         # 1-2 verified above
    write_anchor(path, chain_key, now=now, **current)            # 3 anchor to the verified head

    appended = append_event(                                     # 4 appends are legal again
        conn,
        chain_key,
        {
            "event_id": mint_event_id(), "ts": now, "tool_name": "admin.repair_anchor",
            "principal_ref": None, "client_ref": None, "account_ref": None, "peer_ref": None,
            "project_ref": None, "project_count": None, "policy_epoch": None,
            "result_count": None, "duration_ms": None, "telegram_rpc_count": None,
            "status": "ok", "error_code": reason or None,
            "disclosure_ref": withheld or None,
        },
    )
    write_anchor(path, chain_key, now=now, **{                   # 5 anchor over that event
        k: appended[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")
    })

    set_setting(conn, "audit.integrity_degraded", 0)             # 6 only now
    set_setting(conn, "audit.degraded_disclosure_ref", "")
    set_setting(conn, "audit.degraded_reason", "")
```

- [ ] **Step 4: Wire the seven operator commands**

`AdminRouter` already validates against the closed §33 list, so no command is added — only handlers. Confirm with:

`AdminRouter` has no membership predicate; the closed list is the module-level
`ADMIN_COMMANDS` tuple, and `AdminRouter.__init__` rejects any handler key not
in it. Verify against that:

Run: `uv run python -c "
from telegram_mcp.ipc.admin import ADMIN_COMMANDS
need = ('disclosure show', 'disclosure verify', 'disclosure key', 'exposure status',
        'audit verify', 'audit checkpoint', 'audit repair-anchor')
for c in need:
    print(c, 'known' if c in ADMIN_COMMANDS else 'MISSING')
"`
Expected: all seven `known` (the tuple holds 51 commands). Register the seven
handlers by passing them in the `handlers` mapping to `AdminRouter`; passing a
key outside `ADMIN_COMMANDS` raises at construction, which is the guard that
keeps the surface closed.

`audit repair-anchor` stays behind the Phase-2a presence gate.

Create `src/telegram_mcp/disclosure/verify.py` — referenced by Step 5 and by
the `disclosure verify` handler, and listed in this plan's File Structure:

```python
"""Reconstruct and verify a persisted receipt (Appendix K.2)."""

from __future__ import annotations

import sqlite3

from telegram_mcp.disclosure.keys import lookup_verification_key
from telegram_mcp.disclosure.receipts import build_proof_payload, verify_proof

_JOIN = """
SELECT r.disclosure_ref, p.principal_ref, c.client_ref, a.account_ref, r.tool_name,
       r.security_epoch, r.policy_epoch, r.project_scope_digest, r.project_count,
       r.effective_egress_level, r.records_disclosed, r.bytes_disclosed, r.partial,
       r.committed_at, r.consent_key_id, r.consent_challenge_digest,
       r.canonical_result_provenance_digest, r.canonical_coverage_digest,
       r.proof_payload_sha256, r.proof_key_id, r.proof_signature
  FROM disclosure_receipts r
  JOIN principals  p ON p.id = r.principal_id
  JOIN mcp_clients c ON c.id = r.client_id
  JOIN accounts    a ON a.id = r.account_id
 WHERE r.disclosure_ref = ?
"""


def verify_persisted_receipt(conn: sqlite3.Connection, disclosure_ref: str) -> bool:
    """Rebuild the canonical payload from the row and check the signature.

    There is no ``proof_payload`` column by design, so the payload is rebuilt
    — which is why ``principal_ref``, ``client_ref`` and ``account_ref`` must
    stay immutable for at least the receipt retention window. A rotation that
    re-mints one of them changes the rebuilt bytes and the signature stops
    verifying, indistinguishably from tampering.
    """
    row = conn.execute(_JOIN, (disclosure_ref,)).fetchone()
    if row is None:
        return False
    names = (
        "disclosure_ref", "principal_ref", "client_ref", "account_ref", "tool_name",
        "security_epoch", "policy_epoch", "project_scope_digest", "project_count",
        "effective_egress_level", "records_disclosed", "bytes_disclosed", "partial",
        "committed_at", "consent_key_id", "consent_challenge_digest",
        "canonical_result_provenance_digest", "canonical_coverage_digest",
        "proof_payload_sha256", "proof_key_id", "proof_signature",
    )
    record = dict(zip(names, row, strict=True))
    signature = record.pop("proof_signature")
    digest = record.pop("proof_payload_sha256")
    key = lookup_verification_key(conn, record.pop("proof_key_id"))
    if key is None:
        return False
    record["partial"] = bool(record["partial"])
    payload = build_proof_payload(**record)
    return verify_proof(
        payload,
        proof_signature=signature,
        proof_payload_sha256=digest,
        public_key_b64url=key["public_key_b64url"],
    )
```

- [ ] **Step 5: Add the ref-rotation test deferred from Plan 3a**

```python
def test_a_rotated_client_credential_does_not_break_an_old_receipt(committed_receipt):
    conn, disclosure_ref = committed_receipt
    # client rotate changes credentials and auth_binding. It must NOT re-mint
    # client_ref: the receipt payload is rebuilt from that ref, so re-minting
    # it would silently invalidate every retained receipt naming this client.
    before = conn.execute("SELECT client_ref FROM mcp_clients WHERE id = 1").fetchone()[0]
    conn.execute("UPDATE mcp_clients SET auth_binding = 'rotated', rotated_at = 'now' WHERE id = 1")
    conn.commit()
    after = conn.execute("SELECT client_ref FROM mcp_clients WHERE id = 1").fetchone()[0]

    assert before == after
    from telegram_mcp.disclosure.verify import verify_persisted_receipt

    assert verify_persisted_receipt(conn, disclosure_ref)
```

- [ ] **Step 6: Run the whole gate**

```bash
uv run pytest -q
uv run python scripts/e2e_smoke.py
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
```

- [ ] **Step 7: Commit**

```bash
git add src/telegram_mcp/disclosure/ src/telegram_mcp/ipc/admin.py src/telegram_mcp/cli.py tests/integration/test_audit_recovery.py
git commit -m "feat: add the anchor repair ceremony and the operator commands"
```

---

### Task 9: The formal model

Appendix L is normative and names both file paths.

**Files:**
- Create: `formal/README.md`, `formal/model.md`
- Modify: `SECURITY-MANIFEST.json` (create if absent)

- [ ] **Step 1: Write `formal/model.md`**

State the twenty state variables — Appendix L's fifteen plus `anchor_epoch`, `anchor_seq`, `audit_integrity_state`, `append_guard_held` and `payload_released` — then the eighteen assertions: Appendix L's fourteen verbatim, plus `NoPayloadBeforeAnchorRefresh`, `ChainNeverMoreThanOneAheadOfAnchor`, `ActualNeverExceedsReserved` and `ExfiltrationHasNoSilentPath`. Then the transitions listed in design §10.3.

- [ ] **Step 2: Choose the checker and record it**

Implement the model as Hypothesis stateful tests in `tests/formal/test_state_machine.py` unless TLA+/TLC is already available on the build host. Record the exact checker, its version, the bounds and the configuration in `formal/README.md` and in `SECURITY-MANIFEST.json`.

- [ ] **Step 3: Run the model and read the full output**

Run: `uv run pytest tests/formal -q`
Expected: PASS with every assertion exercised. An assertion that never fires is not passing — it is unreachable, which is the failure mode this task exists to prevent. Print the per-assertion hit counts and confirm each is non-zero.

- [ ] **Step 4: Commit**

```bash
git add formal/ SECURITY-MANIFEST.json tests/formal/
git commit -m "feat: add the bounded formal safety model"
```

---

### Task 10: Phase-3 evidence and the adversarial benchmark

**Files:**
- Create: `tests/adversarial/test_maximal_extraction.py`
- Modify: `docs/verification/phase-3.md`, `AGENT.md`, `CHANGELOG.md`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: Write the extraction benchmark**

A scripted client extracts as much as the rules permit from the fake adapter over a simulated twenty-four hours under a named synthetic corpus and the default budget configuration. Record the achieved records and bytes.

- [ ] **Step 2: Run it once and seal the result**

Run: `uv run pytest tests/adversarial -q -s`
Record whatever it produces in `docs/verification/phase-3.md`, first run sealed. Do not re-run until the number looks better. Either the budgets hold it to the expected ceiling, or the run found a gap — both are results.

State the figure as **benchmark-observed**: it is what one scripted client extracted from a fake adapter under a named corpus and a named configuration, it is not a measurement of real private content, and it is a lower bound on what a cleverer client might achieve.

- [ ] **Step 3: Complete the Gate O/P/Q ledger in `docs/verification/phase-3.md`**

One row per gate, the exact command that proves each row, and an honest PARTIAL wherever a gate is not fully met — every gate stays PARTIAL until Phase 4 puts a real adapter behind the seam.

- [ ] **Step 4: Update `CLAUDE.md` and `README.md`**

Move Phase 3 from "has not started" to its real state, add the new verify commands, and add `disclosure/` to the module map.

- [ ] **Step 5: Append the dated `**Raouf:**` entry to `AGENT.md` and `CHANGELOG.md`**

- [ ] **Step 6: Commit**

```bash
git add tests/adversarial/ docs/verification/phase-3.md AGENT.md CHANGELOG.md README.md CLAUDE.md
git commit -m "docs: record Phase-3 evidence and the extraction benchmark"
```

---

## Self-Review

**Spec coverage:** §1.2 coordinator interface → Task 5. §2 twelve steps and barriers → Tasks 5 and 6. §6.1 MAC → Task 2. §6.2 genesis and epochs → Task 2. §6.3 anchor → Task 3. §6.4 checkpoints → Task 4. §6.5 append guard and non-tool vocabulary → Tasks 2 and 5. §6.6 degraded is unauditable → Task 6. §6.7 integrity table → Task 3. §6.8 degraded record and repair → Tasks 6 and 8. §7 error mapping → Tasks 5 and 6. §7A operator surface → Task 8. §9.2 eleven settings rows → Task 1. §9.3 Phase-5 constraints → recorded in Task 10's evidence. §10.1 gate mapping → Task 10. §10.2 suites → Tasks 6, 7 and 10. §10.3 formal model → Task 9. §4.3 ref-rotation test → Task 8 Step 5, deferred here from Plan 3a because it needs a committed receipt row.

**Placeholder scan:** Task 5 Step 3 gives the module head and the guarded steps 11–12 in full and directs the implementer to write steps 1–10 as named private methods in `DISCLOSURE_STEPS` order; every other step carries complete code. Task 9 names the artefacts and the acceptance condition rather than the model text, because the checker choice is made at implementation time and recorded in the manifest — that is the design's explicit instruction, not an omission.

**Type consistency:** `ChainError` and `AnchorError` keep their modules across Tasks 2, 3, 4 and 8. `head(conn)` returns the same four keys everywhere it is consumed. `write_anchor` takes the same keyword names in Tasks 3 and 8. `DISCLOSURE_STEPS` is asserted in Task 5 and parametrised in Task 6.
