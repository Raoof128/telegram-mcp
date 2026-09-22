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
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from telegram_mcp.disclosure.measure import (
    RECORD_ELEMENT,
    bytes_disclosed,
    project_bytes,
    records_disclosed,
)
from telegram_mcp.keys.store import load_key
from telegram_mcp.opaque import mint_opaque_ref
from telegram_mcp.storage.settings import get_setting

__all__ = [
    "GLOBAL",
    "PROJECT",
    "BucketKey",
    "BudgetError",
    "BudgetLedger",
    "Reservation",
    "Thresholds",
    "Usage",
    "buckets_for",
    "committed_usage",
    "subject_digest",
    "thresholds_for",
    "tier",
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

    def consult(self, worst_case: Mapping[BucketKey, Usage]) -> tuple[str, dict[BucketKey, Usage]]:
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
