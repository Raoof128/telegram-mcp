"""The audit integrity latch (comms v0.3 A8): one row in comms.db.

While degraded, no new external effect may start; recording and recovering work
already started, reads, verify, repair and doctor stay available.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.storage.db import write_tx

__all__ = [
    "AuditIntegrityDegraded",
    "clear_degraded",
    "is_degraded",
    "latch_degraded",
    "require_not_degraded",
]


class AuditIntegrityDegraded(Exception):
    """A new external effect was refused because the audit trail is degraded."""

    def __init__(self) -> None:
        super().__init__("AUDIT_INTEGRITY_DEGRADED")


def is_degraded(conn: Any) -> bool:
    return (
        conn.execute("SELECT state FROM audit_integrity WHERE id = 1").fetchone()[0] == "degraded"
    )


def latch_degraded(conn: Any, *, reason: str, now: datetime) -> None:
    """Set the latch in its own transaction. Idempotent: the first reason is kept."""
    with write_tx(conn):
        conn.execute(
            "UPDATE audit_integrity SET state = 'degraded', reason = coalesce(reason, ?),"
            " since = coalesce(since, ?) WHERE id = 1 AND state = 'ok'",
            (reason, timeutil.iso(now)),
        )


def require_not_degraded(conn: Any) -> None:
    """Called at every new-effect boundary: a claim, an execution, a scheduled start,
    a provider mutation, and any retention, import or rotation mutation."""
    if is_degraded(conn):
        raise AuditIntegrityDegraded


def clear_degraded(conn: Any, *, now: datetime) -> None:
    """Only ``audit repair`` calls this, after its own verifier passes (Task B21)."""
    with write_tx(conn):
        conn.execute(
            "UPDATE audit_integrity SET state = 'ok', reason = NULL, since = NULL WHERE id = 1"
        )
