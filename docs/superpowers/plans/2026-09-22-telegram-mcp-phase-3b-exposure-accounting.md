# Telegram MCP Phase 3b — Exposure Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared mutable privacy state of Phase 3 — rolling exposure windows over two budget dimensions, the pre-consent and post-consent budget consultations, and the reservation lifecycle that makes a hard ceiling unbypassable by concurrency.

**Architecture:** One module, `src/telegram_mcp/disclosure/budget.py`, holding a `BudgetLedger` that owns a single reservation lock and the live in-memory reservations. Committed usage is recomputed from `exposure_ledger` rows inside the rolling window on every consultation — never cached, because a cached ceiling is a bypassable ceiling. Every quantity comes from Plan 3a's `measure.py`; this plan computes no bytes of its own. Nothing here writes the ledger outside a transaction Plan 3c supplies.

**Tech Stack:** Python 3.12, stdlib `sqlite3` and `threading`, `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md` (revision 3). Read §5 in full before starting. The frozen contract is `telegram-mcp-v0.1.10-final-engineering-spec.md` §23C.

**Depends on:** Plan 3a Tasks 2 and 7 (`measure.py`, the key store providing `privacy-key`).

## Global Constraints

- **Never commit or log Telegram credentials, session files, keys or private material.** No project display name, peer name or query text may reach `exposure_ledger`.
- **No production claim** until Gates A–R pass. Phase 3b moves parts of Gate P only.
- **Fail closed.** A budget that cannot be computed refuses; it never permits.
- **No test-only flags in production paths.** The clock and the lock are injected seams, not `if testing:` branches.
- **One copy of each shared rule.** All record and byte quantities come from `telegram_mcp.disclosure.measure`. Computing a byte count here is the defect.
- **Over-count, never under-count.** Where an outcome is uncertain, the exposure stays charged.
- **Uniform `ValueError` on validation**, with `# noqa: TRY004 -- <reason>` where ruff objects.
- **Read `AGENT.md` and `CHANGELOG.md` before editing**, and append a dated `**Raouf:**` entry to both afterwards.
- Verification gate for every task: `uv run pytest -q`, `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src/telegram_mcp`.

## File Structure

| File | Responsibility |
|---|---|
| `src/telegram_mcp/disclosure/budget.py` | subject digests, rolling windows, tiers, reservations, ledger commit |
| `tests/unit/test_disclosure_budget.py` | windows, dimensions, tiers, projection |
| `tests/integration/test_budget_concurrency.py` | two callers cannot both see the same remaining capacity |
| `tests/security/test_budget_bypass.py` | no argument, retry or project cycle beats a hard ceiling |

## Budget subjects

`exposure_ledger.budget_subject_digest` is `NOT NULL` for both dimensions, and the spec does not fix its derivation. This plan freezes it as a keyed HMAC so no opaque ref lands in the ledger at all:

```text
budget_subject_digest = HMAC-SHA-256(
    privacy_key,
    "telegram-mcp-budget-subject-v1" || kind || subject
)                                     hex, lowercase
    kind = "client_global"  -> subject = ""
    kind = "project"        -> subject = the tpr_ project ref
```

It is deterministic, so `exposure status --project <p>` recomputes it rather than storing a lookup, and it is keyed, so the ledger reveals nothing about which projects exist to anyone who reads the table without the key.

---

### Task 1: Subject digests and the rolling window

**Files:**
- Create: `src/telegram_mcp/disclosure/budget.py`
- Test: `tests/unit/test_disclosure_budget.py`

**Interfaces:**
- Consumes: `load_key` from `telegram_mcp.keys.store`.
- Produces:
  - `GLOBAL, PROJECT: str` — the two `budget_subject_kind` values.
  - `subject_digest(kind: str, subject: str = "") -> str`
  - `BucketKey` — frozen dataclass `(client_id: int, kind: str, subject_digest: str)`
  - `Usage` — frozen dataclass `(records: int, bytes: int)` with `__add__`
  - `window_start(now: float, minutes: int) -> str` — ISO-8601 `Z`
  - `BudgetError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_budget.py`:

```python
"""Budget subjects, windows and dimensions (design §5.2; frozen spec §23C)."""

import pytest

from telegram_mcp.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    Usage,
    subject_digest,
    window_start,
)
from telegram_mcp.keys.store import provision_missing

_P = "tpr_" + "a" * 26


@pytest.fixture(autouse=True)
def _keys(tmp_path):
    provision_missing(tmp_path, phases=(2,))


def test_subject_digest_is_deterministic_keyed_hex():
    first = subject_digest(PROJECT, _P)
    assert first == subject_digest(PROJECT, _P)
    assert len(first) == 64 and first == first.lower()
    int(first, 16)


def test_the_two_dimensions_never_collide():
    assert subject_digest(GLOBAL) != subject_digest(PROJECT, _P)
    assert subject_digest(PROJECT, _P) != subject_digest(PROJECT, "tpr_" + "b" * 26)


def test_digest_carries_no_recoverable_ref():
    # The ref must not appear anywhere in the digest's text form.
    assert _P not in subject_digest(PROJECT, _P)


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        subject_digest("vibes", _P)


def test_window_start_is_iso_z_and_moves_with_the_clock():
    early = window_start(1_800_000_000.0, 30)
    later = window_start(1_800_003_600.0, 30)
    assert early.endswith("Z") and "T" in early
    assert later > early


def test_usage_adds_componentwise():
    assert Usage(1, 10) + Usage(2, 20) == Usage(3, 30)


def test_bucket_key_is_hashable_for_use_as_a_dict_key():
    key = BucketKey(client_id=1, kind=GLOBAL, subject_digest=subject_digest(GLOBAL))
    assert {key: Usage(0, 0)}[key] == Usage(0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.budget'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/budget.py`:

```python
"""Cumulative exposure budgets (frozen spec §23C; design §5).

Two dimensions, not two buckets: ``client_global``, and ``(client, origin
project)`` for every contributing project. A cross-project search over three
projects therefore touches four physical buckets.

Committed usage is recomputed from ``exposure_ledger`` on every consultation.
Nothing is cached: a cached ceiling is a bypassable ceiling.

All record and byte quantities come from ``telegram_mcp.disclosure.measure``.
Computing one here would give the prompt and the ledger two different truths,
which is exactly what Gate P forbids.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from telegram_mcp.keys.store import load_key

__all__ = [
    "GLOBAL",
    "PROJECT",
    "BucketKey",
    "BudgetError",
    "Usage",
    "subject_digest",
    "window_start",
]

# Mirrors the CHECK on exposure_ledger.budget_subject_kind (spec §12.2).
GLOBAL = "client_global"
PROJECT = "project"
_KINDS = (GLOBAL, PROJECT)

_SUBJECT_DOMAIN = b"telegram-mcp-budget-subject-v1"


class BudgetError(Exception):
    """A budget could not be computed, or a ceiling was reached."""


@dataclass(frozen=True)
class BucketKey:
    """One accounting bucket: a client in one of the two dimensions."""

    client_id: int
    kind: str
    subject_digest: str


@dataclass(frozen=True)
class Usage:
    """Records and bytes, the two quantities every ceiling is expressed in."""

    records: int
    bytes: int

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.records + other.records, self.bytes + other.bytes)


def subject_digest(kind: str, subject: str = "") -> str:
    """Keyed, deterministic digest so no opaque ref lands in the ledger."""
    if kind not in _KINDS:
        raise ValueError("unknown budget subject kind")
    if kind == GLOBAL and subject:
        raise ValueError("client_global takes no subject")
    if kind == PROJECT and not subject:
        raise ValueError("project subject is required")
    key = load_key("privacy-key")
    message = _SUBJECT_DOMAIN + kind.encode("ascii") + subject.encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def window_start(now: float, minutes: int) -> str:
    """Start of the rolling window, as the ISO-8601 ``Z`` form the ledger uses."""
    if minutes <= 0:
        raise ValueError("rolling window must be positive")
    started = datetime.fromtimestamp(now - minutes * 60, tz=UTC)
    return started.strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/budget.py tests/unit/test_disclosure_budget.py
git commit -m "feat: add budget subject digests and rolling windows"
```

---

### Task 2: Committed usage and the bucket set for a call

**Files:**
- Modify: `src/telegram_mcp/disclosure/budget.py`
- Test: `tests/unit/test_disclosure_budget.py`

**Interfaces:**
- Consumes: Task 1's `BucketKey`, `Usage`, `subject_digest`, `window_start`; `project_bytes`, `container_bytes`, `records_disclosed`, `bytes_disclosed` from `telegram_mcp.disclosure.measure`.
- Produces:
  - `committed_usage(conn, key: BucketKey, *, since: str) -> Usage`
  - `buckets_for(tool_name: str, data, *, client_id: int) -> dict[BucketKey, Usage]`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_disclosure_budget.py`:

```python
def test_catalogue_tools_charge_only_the_global_bucket():
    from telegram_mcp.disclosure.budget import GLOBAL, buckets_for

    data = {"projects": [{"project_ref": _P, "origin_project_refs": []}]}
    buckets = buckets_for("telegram_list_projects", data, client_id=1)

    assert len(buckets) == 1
    assert next(iter(buckets)).kind == GLOBAL


def test_three_projects_touch_four_buckets():
    from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, buckets_for

    refs = ["tpr_" + c * 26 for c in "abc"]
    data = {
        "projects": [{"project_ref": r} for r in refs],
        "search_scope": {"mode": "cross_project"},
        "results": [
            {"message_ref": "tgm_" + "a" * 26, "origin_project_refs": [refs[0]]},
            {"message_ref": "tgm_" + "b" * 26, "origin_project_refs": [refs[1]]},
            {"message_ref": "tgm_" + "c" * 26, "origin_project_refs": [refs[2]]},
        ],
    }
    buckets = buckets_for("telegram_cross_project_search", data, client_id=1)

    assert len(buckets) == 4
    assert sum(1 for k in buckets if k.kind == PROJECT) == 3
    assert sum(1 for k in buckets if k.kind == GLOBAL) == 1


def test_a_shared_record_is_charged_once_globally_and_once_per_project():
    from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, buckets_for

    a, b = "tpr_" + "a" * 26, "tpr_" + "b" * 26
    data = {
        "projects": [{"project_ref": a}, {"project_ref": b}],
        "search_scope": {"mode": "cross_project"},
        "results": [{"message_ref": "tgm_" + "a" * 26, "origin_project_refs": [a, b]}],
    }
    buckets = buckets_for("telegram_cross_project_search", data, client_id=1)

    glob = next(u for k, u in buckets.items() if k.kind == GLOBAL)
    projects = [u for k, u in buckets.items() if k.kind == PROJECT]
    assert glob.records == 1
    assert [u.records for u in projects] == [1, 1]
    # Deduplication at the retrieval layer must never reach accounting.
    assert sum(u.records for u in projects) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -k buckets -v`
Expected: FAIL with `ImportError: cannot import name 'buckets_for'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/telegram_mcp/disclosure/budget.py` (and add the imports and `__all__` entries):

```python
def committed_usage(conn: sqlite3.Connection, key: BucketKey, *, since: str) -> Usage:
    """Sum the ledger rows for one bucket inside the rolling window."""
    row = conn.execute(
        "SELECT COALESCE(SUM(records_disclosed), 0), COALESCE(SUM(bytes_disclosed), 0)"
        " FROM exposure_ledger"
        " WHERE client_id = ? AND budget_subject_kind = ?"
        "   AND budget_subject_digest = ? AND ts >= ?",
        (key.client_id, key.kind, key.subject_digest, since),
    ).fetchone()
    return Usage(int(row[0]), int(row[1]))


def buckets_for(
    tool_name: str, data: Mapping[str, Any], *, client_id: int
) -> dict[BucketKey, Usage]:
    """Every bucket this response charges, with the quantity for each.

    The global bucket takes the whole ``data`` object's canonical bytes and
    every record. Each contributing project takes its own records and the
    canonical bytes of the records attributed to it — ``data``-container
    overhead is global only, and ``meta`` (including ``coverage`` and the
    proof envelope) is outside the measurement entirely.
    """
    global_key = BucketKey(client_id, GLOBAL, subject_digest(GLOBAL))
    buckets: dict[BucketKey, Usage] = {
        global_key: Usage(records_disclosed(tool_name, data), bytes_disclosed(data))
    }

    per_project_bytes = project_bytes(tool_name, data)
    per_project_records: dict[str, int] = {}
    for record in data.get(RECORD_ELEMENT[tool_name], []):
        for project_ref in record.get("origin_project_refs") or []:
            per_project_records[project_ref] = per_project_records.get(project_ref, 0) + 1

    for project_ref, size in per_project_bytes.items():
        key = BucketKey(client_id, PROJECT, subject_digest(PROJECT, project_ref))
        buckets[key] = Usage(per_project_records.get(project_ref, 0), size)

    return buckets
```

Add at the top of the module:

```python
import sqlite3
from collections.abc import Mapping
from typing import Any

from telegram_mcp.disclosure.measure import (
    RECORD_ELEMENT,
    bytes_disclosed,
    project_bytes,
    records_disclosed,
)
```

and extend `__all__` with `"buckets_for"` and `"committed_usage"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/budget.py tests/unit/test_disclosure_budget.py
git commit -m "feat: compute the bucket set and committed usage for a call"
```

---

### Task 3: Tiers and the projected comparison

§23C.3 compares the **projected** figure — committed rows, plus live reservations, plus this call's worst case. Comparing current totals alone would bound where a call starts rather than where it ends.

**Files:**
- Modify: `src/telegram_mcp/disclosure/budget.py`
- Test: `tests/unit/test_disclosure_budget.py`

**Interfaces:**
- Produces:
  - `Thresholds` — frozen dataclass `(soft_records, hard_records, soft_bytes, hard_bytes)`
  - `thresholds_for(conn, kind: str) -> Thresholds`
  - `tier(projected: Usage, limits: Thresholds) -> str` returning `"normal" | "elevated" | "refuse"`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_disclosure_budget.py`:

```python
def test_tiers_follow_the_frozen_baseline():
    from telegram_mcp.disclosure.budget import Thresholds, Usage, tier

    limits = Thresholds(soft_records=100, hard_records=500, soft_bytes=500_000, hard_bytes=5_000_000)

    assert tier(Usage(99, 0), limits) == "normal"
    assert tier(Usage(100, 0), limits) == "elevated"     # at the soft threshold
    assert tier(Usage(499, 0), limits) == "elevated"
    assert tier(Usage(500, 0), limits) == "refuse"       # at the hard threshold
    assert tier(Usage(0, 500_000), limits) == "elevated"
    assert tier(Usage(0, 5_000_000), limits) == "refuse"


def test_either_quantity_alone_can_refuse():
    from telegram_mcp.disclosure.budget import Thresholds, Usage, tier

    limits = Thresholds(soft_records=100, hard_records=500, soft_bytes=500_000, hard_bytes=5_000_000)
    assert tier(Usage(0, 5_000_001), limits) == "refuse"
    assert tier(Usage(501, 0), limits) == "refuse"


def test_thresholds_are_read_from_the_settings_registry(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, thresholds_for
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    conn = open_db(tmp_path / "meta.db")
    migrate(conn)

    assert thresholds_for(conn, PROJECT).hard_records == 500
    assert thresholds_for(conn, GLOBAL).hard_records == 1_500
    assert thresholds_for(conn, GLOBAL).hard_bytes == 15_000_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -k tier -v`
Expected: FAIL with `ImportError: cannot import name 'tier'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/telegram_mcp/disclosure/budget.py`:

```python
@dataclass(frozen=True)
class Thresholds:
    """One dimension's soft and hard ceilings, in both quantities."""

    soft_records: int
    hard_records: int
    soft_bytes: int
    hard_bytes: int


def thresholds_for(conn: sqlite3.Connection, kind: str) -> Thresholds:
    """Read the §23C.1 baseline from the closed settings registry."""
    if kind not in _KINDS:
        raise ValueError("unknown budget subject kind")
    suffix = "client_global" if kind == GLOBAL else "client_project"
    return Thresholds(
        soft_records=get_setting(conn, f"exposure_budget.soft_records_per_{suffix}"),
        hard_records=get_setting(conn, f"exposure_budget.hard_records_per_{suffix}"),
        soft_bytes=get_setting(conn, f"exposure_budget.soft_bytes_per_{suffix}"),
        hard_bytes=get_setting(conn, f"exposure_budget.hard_bytes_per_{suffix}"),
    )


def tier(projected: Usage, limits: Thresholds) -> str:
    """``normal`` | ``elevated`` | ``refuse`` for a projected figure.

    "At or above" is the spec's wording for both thresholds, so equality
    escalates rather than passing (spec §23C.3).
    """
    if projected.records >= limits.hard_records or projected.bytes >= limits.hard_bytes:
        return "refuse"
    if projected.records >= limits.soft_records or projected.bytes >= limits.soft_bytes:
        return "elevated"
    return "normal"
```

Add `from telegram_mcp.storage.settings import get_setting` to the imports and extend `__all__` with `"Thresholds"`, `"thresholds_for"`, `"tier"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/budget.py tests/unit/test_disclosure_budget.py
git commit -m "feat: add budget thresholds and the projected tier decision"
```

---

### Task 4: The reservation lifecycle

**Files:**
- Modify: `src/telegram_mcp/disclosure/budget.py`
- Test: `tests/unit/test_disclosure_budget.py`

**Interfaces:**
- Produces:
  - `Reservation` — frozen dataclass with the six frozen binding components plus bookkeeping: `(reservation_ref, client_id, security_epoch, project_scope_digest, consent_challenge_digest, request_nonce, expires_at, buckets)`
  - `BudgetLedger(conn, *, clock=time.time)` with:
    - `.consult(tool_name, data, *, client_id) -> tuple[str, dict[BucketKey, Usage]]`
    - `.reserve(*, client_id, security_epoch, project_scope_digest, consent_challenge_digest, request_nonce, worst_case, ttl_seconds) -> Reservation`
    - `.release(reservation_ref) -> None`
    - `.live_usage(key: BucketKey) -> Usage`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_disclosure_budget.py`:

```python
def _ledger(tmp_path):
    from telegram_mcp.disclosure.budget import BudgetLedger
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    return BudgetLedger(conn)


def _worst(client_id=1, records=10, size=1000):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, Usage, subject_digest

    return {BucketKey(client_id, GLOBAL, subject_digest(GLOBAL)): Usage(records, size)}


def test_reservation_binds_the_six_frozen_components(tmp_path):
    ledger = _ledger(tmp_path)
    reservation = ledger.reserve(
        client_id=1,
        security_epoch=4,
        project_scope_digest="hmac-sha256:" + "0" * 64,
        consent_challenge_digest="1" * 64,
        request_nonce="n" * 32,
        worst_case=_worst(),
        ttl_seconds=60,
    )
    # §23C.3 freezes exactly these; dropping security_epoch or the challenge
    # digest would let an emergency lock or a different approval be ignored.
    assert reservation.client_id == 1
    assert reservation.security_epoch == 4
    assert reservation.project_scope_digest == "hmac-sha256:" + "0" * 64
    assert reservation.consent_challenge_digest == "1" * 64
    assert reservation.request_nonce == "n" * 32
    assert reservation.expires_at > 0
    assert reservation.reservation_ref


def test_a_live_reservation_counts_against_the_next_consultation(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, subject_digest

    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    assert ledger.live_usage(key).records == 0

    ledger.reserve(
        client_id=1, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n", worst_case=_worst(records=7), ttl_seconds=60,
    )
    assert ledger.live_usage(key).records == 7


def test_release_frees_the_reservation(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, subject_digest

    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    reservation = ledger.reserve(
        client_id=1, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n", worst_case=_worst(records=7), ttl_seconds=60,
    )
    ledger.release(reservation.reservation_ref)
    assert ledger.live_usage(key).records == 0


def test_an_expired_reservation_stops_counting(tmp_path):
    from telegram_mcp.disclosure.budget import BudgetLedger, GLOBAL, BucketKey, subject_digest
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    now = [1_800_000_000.0]
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    ledger = BudgetLedger(conn, clock=lambda: now[0])
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    ledger.reserve(
        client_id=1, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n", worst_case=_worst(records=7), ttl_seconds=60,
    )
    assert ledger.live_usage(key).records == 7
    now[0] += 61
    assert ledger.live_usage(key).records == 0


def test_reserving_past_a_hard_ceiling_refuses(tmp_path):
    import pytest

    from telegram_mcp.disclosure.budget import BudgetError

    ledger = _ledger(tmp_path)
    with pytest.raises(BudgetError):
        ledger.reserve(
            client_id=1, security_epoch=1, project_scope_digest="d",
            consent_challenge_digest="c", request_nonce="n",
            worst_case=_worst(records=2_000),  # global hard ceiling is 1500
            ttl_seconds=60,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -k reserv -v`
Expected: FAIL with `ImportError: cannot import name 'BudgetLedger'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/telegram_mcp/disclosure/budget.py`:

```python
@dataclass(frozen=True)
class Reservation:
    """Memory-only capability state, never MCP-visible (spec §23C.3).

    The first six fields are the binding §23C.3 freezes. ``security_epoch``
    is what makes an emergency lock invalidate reservations in flight;
    ``consent_challenge_digest`` is what stops a reservation minted under one
    approval being spent by a different call.
    """

    reservation_ref: str
    client_id: int
    security_epoch: int
    project_scope_digest: str
    consent_challenge_digest: str
    request_nonce: str
    expires_at: float
    buckets: tuple[tuple[BucketKey, Usage], ...]


class BudgetLedger:
    """Owns the single reservation lock and the live reservations.

    The lock is held while buckets are recomputed and a reservation is
    created, and released before retrieval — never held across a Telegram
    call. Plan 3c converts a reservation into ledger rows inside its
    disclosure-commit transaction.
    """

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], float] = time.time) -> None:
        self._conn = conn
        self._clock = clock
        self._lock = threading.Lock()
        self._reservations: dict[str, Reservation] = {}

    # -- live state ---------------------------------------------------------

    def _prune(self) -> None:
        now = self._clock()
        expired = [ref for ref, r in self._reservations.items() if r.expires_at <= now]
        for ref in expired:
            del self._reservations[ref]

    def live_usage(self, key: BucketKey) -> Usage:
        """Unexpired reserved quantity for one bucket."""
        with self._lock:
            self._prune()
            total = Usage(0, 0)
            for reservation in self._reservations.values():
                for bucket, usage in reservation.buckets:
                    if bucket == key:
                        total = total + usage
            return total

    # -- consultations ------------------------------------------------------

    def _projected(self, key: BucketKey, increment: Usage) -> Usage:
        minutes = get_setting(self._conn, "exposure_budget.rolling_window_minutes")
        since = window_start(self._clock(), minutes)
        committed = committed_usage(self._conn, key, since=since)
        live = Usage(0, 0)
        for reservation in self._reservations.values():
            for bucket, usage in reservation.buckets:
                if bucket == key:
                    live = live + usage
        return committed + live + increment

    def consult(
        self, worst_case: Mapping[BucketKey, Usage]
    ) -> tuple[str, dict[BucketKey, Usage]]:
        """Pre-consent evaluation: the strictest tier across every bucket.

        Returns the tier and the projected figure per bucket, which is what
        the trusted prompt displays as "current/projected".
        """
        with self._lock:
            self._prune()
            projected: dict[BucketKey, Usage] = {}
            worst_tier = "normal"
            for key, increment in worst_case.items():
                figure = self._projected(key, increment)
                projected[key] = figure
                decision = tier(figure, thresholds_for(self._conn, key.kind))
                if decision == "refuse" or (decision == "elevated" and worst_tier == "normal"):
                    worst_tier = decision
            return worst_tier, projected

    # -- reservations -------------------------------------------------------

    def reserve(
        self,
        *,
        client_id: int,
        security_epoch: int,
        project_scope_digest: str,
        consent_challenge_digest: str,
        request_nonce: str,
        worst_case: Mapping[BucketKey, Usage],
        ttl_seconds: int,
    ) -> Reservation:
        """Recompute under the lock and reserve the worst case, or refuse."""
        with self._lock:
            self._prune()
            for key, increment in worst_case.items():
                figure = self._projected(key, increment)
                if tier(figure, thresholds_for(self._conn, key.kind)) == "refuse":
                    raise BudgetError("hard exposure threshold reached")

            reservation = Reservation(
                reservation_ref=mint_opaque_ref("tgl_"),
                client_id=client_id,
                security_epoch=security_epoch,
                project_scope_digest=project_scope_digest,
                consent_challenge_digest=consent_challenge_digest,
                request_nonce=request_nonce,
                expires_at=self._clock() + ttl_seconds,
                buckets=tuple(worst_case.items()),
            )
            self._reservations[reservation.reservation_ref] = reservation
            return reservation

    def release(self, reservation_ref: str) -> None:
        """Release on cancellation, stale authority, failure — any non-commit path."""
        with self._lock:
            self._reservations.pop(reservation_ref, None)
```

Add to the imports:

```python
import threading
import time
from collections.abc import Callable

from telegram_mcp.opaque import mint_opaque_ref
```

and extend `__all__` with `"BudgetLedger"` and `"Reservation"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/budget.py tests/unit/test_disclosure_budget.py
git commit -m "feat: add the exposure reservation lifecycle"
```

---

### Task 5: Commit a reservation into ledger rows

Plan 3c calls this inside its disclosure-commit transaction. It never commits the connection itself — the caller owns the transaction.

**Files:**
- Modify: `src/telegram_mcp/disclosure/budget.py`
- Test: `tests/unit/test_disclosure_budget.py`

**Interfaces:**
- Produces: `BudgetLedger.commit(reservation, *, disclosure_ref: str, actual: Mapping[BucketKey, Usage], effective_egress_level: str, ts: str) -> None`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_disclosure_budget.py`:

```python
def test_commit_writes_one_row_per_bucket_and_releases(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, Usage, subject_digest

    ledger = _ledger(tmp_path)
    conn = ledger._conn  # noqa: SLF001 -- test inspects the ledger it created
    # seed_authority_rows lives in tests/conftest.py and inserts one account,
    # principal, client and project. Use it rather than hand-written SQL:
    # disclosure_receipts carries a tuple-consistency trigger
    # (disclosure_receipts_tuple_consistency_insert) that rejects a receipt
    # whose principal, client and account disagree, so an ad-hoc INSERT is
    # a fixture that fails for reasons unrelated to what is under test.
    seed_authority_rows(conn)
    insert_committed_receipt(conn, disclosure_ref="tdr_a", records=3, size=30)

    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    reservation = ledger.reserve(
        client_id=1, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n", worst_case={key: Usage(10, 1000)}, ttl_seconds=60,
    )
    ledger.commit(
        reservation, disclosure_ref="tdr_a", actual={key: Usage(3, 30)},
        effective_egress_level="metadata_only", ts="2026-09-22T00:00:00Z",
    )

    rows = conn.execute("SELECT records_disclosed, bytes_disclosed FROM exposure_ledger").fetchall()
    assert rows == [(3, 30)]
    assert ledger.live_usage(key).records == 0


def test_actual_exceeding_reserved_fails_closed(tmp_path):
    import pytest

    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, BudgetError, Usage, subject_digest

    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    reservation = ledger.reserve(
        client_id=1, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n", worst_case={key: Usage(10, 1000)}, ttl_seconds=60,
    )
    # The estimator was wrong. That is not a licence to charge more.
    with pytest.raises(BudgetError):
        ledger.commit(
            reservation, disclosure_ref="tdr_a", actual={key: Usage(11, 1000)},
            effective_egress_level="metadata_only", ts="2026-09-22T00:00:00Z",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -k commit -v`
Expected: FAIL with `AttributeError: 'BudgetLedger' object has no attribute 'commit'`.

- [ ] **Step 3: Write minimal implementation**

Append to the `BudgetLedger` class:

```python
    def commit(
        self,
        reservation: Reservation,
        *,
        disclosure_ref: str,
        actual: Mapping[BucketKey, Usage],
        effective_egress_level: str,
        ts: str,
    ) -> None:
        """Convert a reservation into ledger rows. The caller owns the transaction.

        **Invariant: actual ≤ reserved.** If measurement exceeds the
        reservation the worst-case estimator is wrong, and that is not a
        licence to charge more — the caller must fail closed with
        ``PROOF_GENERATION_FAILED`` and emit no payload. Otherwise an
        attacker who found an estimator gap could exceed an approved
        exposure.
        """
        reserved = dict(reservation.buckets)
        for key, measured in actual.items():
            limit = reserved.get(key)
            if limit is None:
                raise BudgetError("measured a bucket that was never reserved")
            if measured.records > limit.records or measured.bytes > limit.bytes:
                raise BudgetError("actual exposure exceeded the reservation")

        for key, measured in actual.items():
            self._conn.execute(
                "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id,"
                " budget_subject_kind, budget_subject_digest, records_disclosed,"
                " bytes_disclosed, effective_egress_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    disclosure_ref,
                    ts,
                    key.client_id,
                    key.kind,
                    key.subject_digest,
                    measured.records,
                    measured.bytes,
                    effective_egress_level,
                ),
            )
        with self._lock:
            self._reservations.pop(reservation.reservation_ref, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_budget.py -v`
Expected: PASS, 20 tests.

- [ ] **Step 4a: Confirm the fixture satisfies the receipt trigger**

Run: `uv run python -c "
import sqlite3, tempfile, pathlib
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate
d = pathlib.Path(tempfile.mkdtemp()); conn = open_db(d/'m.db'); migrate(conn)
print([r[0] for r in conn.execute(
    \"SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='disclosure_receipts'\").fetchall()])
"`
Expected: `['disclosure_receipts_tuple_consistency_insert']`. If
`insert_committed_receipt` ever trips it, fix the fixture's ids — never the
trigger. The trigger is a §12.3 integrity rule, not test friction.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/budget.py tests/unit/test_disclosure_budget.py
git commit -m "feat: commit reservations into exposure ledger rows"
```

---

### Task 6: Concurrency and bypass suites

**Files:**
- Create: `tests/integration/test_budget_concurrency.py`, `tests/security/test_budget_bypass.py`

- [ ] **Step 1: Write the concurrency test**

Create `tests/integration/test_budget_concurrency.py`:

```python
"""Two callers cannot both see the same remaining capacity (Gate P)."""

import threading

from telegram_mcp.disclosure.budget import (
    GLOBAL,
    BucketKey,
    BudgetError,
    BudgetLedger,
    Usage,
    subject_digest,
)
from telegram_mcp.keys.store import provision_missing
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate


def test_concurrent_reservations_cannot_both_take_the_last_capacity(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2,))
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    ledger = BudgetLedger(conn)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    # The global hard ceiling is 1500 records. Two callers each want 800:
    # one must win, one must be refused. If both won, the ceiling would be
    # advisory rather than enforced.
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt(nonce: str) -> None:
        barrier.wait()
        try:
            ledger.reserve(
                client_id=1, security_epoch=1, project_scope_digest="d",
                consent_challenge_digest="c", request_nonce=nonce,
                worst_case={key: Usage(800, 0)}, ttl_seconds=60,
            )
            outcomes.append("reserved")
        except BudgetError:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(f"n{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["refused", "reserved"]
    assert ledger.live_usage(key).records == 800
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/integration/test_budget_concurrency.py -v`
Expected: PASS. If it flakes, the lock is not covering the recompute — fix the lock, never the test.

- [ ] **Step 3: Write the bypass test**

Create `tests/security/test_budget_bypass.py`:

```python
"""No argument, retry or project cycle beats a hard ceiling (Gate P, design §5.6)."""

import pytest

from telegram_mcp.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    BudgetError,
    BudgetLedger,
    Usage,
    subject_digest,
)
from telegram_mcp.keys.store import provision_missing
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate


def _ledger(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2,))
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    return BudgetLedger(conn)


def test_retrying_does_not_accumulate_past_the_ceiling(tmp_path):
    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    for i in range(1):  # 1 x 800 reserved, 800 live
        ledger.reserve(
            client_id=1, security_epoch=1, project_scope_digest="d",
            consent_challenge_digest="c", request_nonce=f"n{i}",
            worst_case={key: Usage(800, 0)}, ttl_seconds=600,
        )
    # A client that simply asks again is refused: live reservations count.
    with pytest.raises(BudgetError):
        ledger.reserve(
            client_id=1, security_epoch=1, project_scope_digest="d",
            consent_challenge_digest="c", request_nonce="retry",
            worst_case={key: Usage(800, 0)}, ttl_seconds=600,
        )


def test_cycling_projects_still_hits_the_client_global_ceiling(tmp_path):
    ledger = _ledger(tmp_path)
    glob = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    # Each call names a different project, so every per-project bucket stays
    # far below its own 500-record ceiling. The global bucket does not care.
    for i in range(2):
        project = BucketKey(1, PROJECT, subject_digest(PROJECT, f"tpr_{chr(97 + i) * 26}"))
        ledger.reserve(
            client_id=1, security_epoch=1, project_scope_digest="d",
            consent_challenge_digest="c", request_nonce=f"n{i}",
            worst_case={glob: Usage(700, 0), project: Usage(700, 0)}, ttl_seconds=600,
        )

    project = BucketKey(1, PROJECT, subject_digest(PROJECT, "tpr_" + "z" * 26))
    with pytest.raises(BudgetError):
        ledger.reserve(
            client_id=1, security_epoch=1, project_scope_digest="d",
            consent_challenge_digest="c", request_nonce="n3",
            worst_case={glob: Usage(700, 0), project: Usage(700, 0)}, ttl_seconds=600,
        )


def test_a_different_client_has_its_own_ceiling(tmp_path):
    ledger = _ledger(tmp_path)
    first = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    second = BucketKey(2, GLOBAL, subject_digest(GLOBAL))

    ledger.reserve(
        client_id=1, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n1", worst_case={first: Usage(1_400, 0)}, ttl_seconds=600,
    )
    # Budgets are per authenticated client; client 2 is unaffected.
    ledger.reserve(
        client_id=2, security_epoch=1, project_scope_digest="d", consent_challenge_digest="c",
        request_nonce="n2", worst_case={second: Usage(1_400, 0)}, ttl_seconds=600,
    )
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/security/test_budget_bypass.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole gate**

```bash
uv run pytest -q
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
```

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_budget_concurrency.py tests/security/test_budget_bypass.py
git commit -m "test: pin budget concurrency and bypass resistance"
```

---

### Task 7: Phase-3b evidence

**Files:**
- Modify: `docs/verification/phase-3.md`, `AGENT.md`, `CHANGELOG.md`

- [ ] **Step 1: Run the full gate and capture real output**

```bash
uv run pytest -q
uv run python scripts/e2e_smoke.py
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
```

- [ ] **Step 2: Update the Gate P row in `docs/verification/phase-3.md`**

Gate P stays PARTIAL after 3b: concurrency, project cycling and measurement identity are proven, but egress profiles are not yet enforced end to end and the emergency lock is not wired to reservations. Name each missing piece and the plan that closes it.

- [ ] **Step 3: Append the dated `**Raouf:**` entry to `AGENT.md` and `CHANGELOG.md`**

State plainly that no tool calls the ledger yet, that no Telegram access occurred, and that no production claim is made.

- [ ] **Step 4: Commit**

```bash
git add docs/verification/phase-3.md AGENT.md CHANGELOG.md
git commit -m "docs: record Phase-3b evidence"
```

---

## Self-Review

**Spec coverage:** §5.1 measurement authority → imported in Task 2, never reimplemented. §5.2 dimensions, subjects, quantities → Tasks 1 and 2. §5.3 both consultations → Task 3 `tier` plus Task 4 `consult` and `reserve`. §5.4 reservation lifecycle and `actual ≤ reserved` → Tasks 4 and 5. §5.6 trichotomy → Task 6. §5.5 re-prompt bound → belongs to the coordinator, carried in Plan 3c.

**Deferred to 3c on purpose:** the consent snapshot digest comparison and the one-restart re-prompt, because both need the consent broker the coordinator owns; and `exposure status`, because it is an admin-router command.

**Type consistency:** `BucketKey`, `Usage` and `Reservation` keep the same field names across Tasks 1, 2, 4 and 5. `worst_case` is a `Mapping[BucketKey, Usage]` in `consult`, `reserve` and `commit`. `thresholds_for(conn, kind)` takes the same `kind` values as `subject_digest`.
