"""Telegram session revoke: two transactions, one ``auth.LogOut``, crash-finished (comms v0.3 B14, O3).

1. **TX1** (legacy DB): ``telegram.session_state = revoking`` and ``security_epoch += 1``, so
   every read answers ``SESSION_REVOKED`` from here on. Then the comms-chain event
   ``admin.session_revoke`` (``started``) is committed **and anchored** before anything is sent.
2. One ``auth.LogOutRequest``, never retried: ``confirmed``, ``failed`` or ``unknown``.
3. The local session is wiped whatever the outcome.
4. **TX2**: ``logged_out`` with the remote outcome, then the ``finished`` event.

At startup ``finish_revocation`` converges any interrupted run without sending again:
``revoking`` without the event appends it first; ``revoking`` finishes with ``unknown``; a
``started`` event without ``revoking`` means TX1 never happened, so it closes as ``aborted``.
Only the auth handler imports this module (pinned by a test).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.audit.writer import AuditWriter
from comms.core.storage.db import write_tx
from comms.transports.telegram.storage.owner_state import load_security
from comms.transports.telegram.storage.settings import get_setting, put_setting
from comms.transports.telegram.telegram.deadline import Deadline
from comms.transports.telegram.telegram.errors import GatewayError

__all__ = [
    "RevokeCrash",
    "apply_session_state",
    "finish_revocation",
    "revoke_session",
    "send_logout_once",
]

_UNKNOWN = frozenset({"TELEGRAM_UNAVAILABLE", "DEADLINE_EXCEEDED"})
_DEADLINE_S = 30.0


class RevokeCrash(BaseException):
    """Raised only by the ``crash_at`` seam, which production never supplies."""


async def send_logout_once(session: Any, deadline: Deadline) -> str:
    """Exactly one ``auth.LogOutRequest``; the outcome, never a retry."""
    try:
        await session.admin_log_out(deadline)
    except GatewayError as exc:
        return "unknown" if exc.code in _UNKNOWN else "failed"
    return "confirmed"


def apply_session_state(legacy_conn: Any, session: Any) -> None:
    """At startup: a revoking or logged-out session serves no read."""
    if get_setting(legacy_conn, "telegram.session_state") != "active":
        session.revoked = True


def _pending_started(comms_conn: Any) -> dict[str, Any] | None:
    rows = comms_conn.execute(
        "SELECT payload FROM audit_events WHERE kind = 'admin.session_revoke'"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
    ).fetchone()
    if rows is None:
        return None
    last = json.loads(rows[0])
    return last if last["phase"] == "started" else None


def _event(writer: AuditWriter, phase: str, outcome: str, epoch: int) -> None:
    with writer.transaction() as tx:  # committed and anchored when the block exits
        tx.append(
            "admin.session_revoke",
            payload={"phase": phase, "outcome": outcome, "security_epoch": epoch},
        )


async def _finish(
    legacy_conn: Any, writer: AuditWriter, session: Any, outcome: str, now: datetime
) -> str:
    await session.logout_local()
    session.revoked = True
    stamp = timeutil.iso(now)
    with write_tx(legacy_conn):
        put_setting(legacy_conn, "telegram.session_state", "logged_out", now=stamp)
        put_setting(legacy_conn, "telegram.remote_revoke", outcome, now=stamp)
    _event(writer, "finished", outcome, load_security(legacy_conn)[0])
    return outcome


async def revoke_session(
    legacy_conn: Any,
    writer: AuditWriter,
    session: Any,
    *,
    now: datetime,
    crash_at: str | None = None,
) -> str:
    def crash(point: str) -> None:
        if crash_at == point:
            raise RevokeCrash(point)

    if get_setting(legacy_conn, "telegram.session_state") != "active":
        raise ValueError("this session is already being revoked or is logged out")
    stamp = timeutil.iso(now)
    with write_tx(legacy_conn):
        put_setting(legacy_conn, "telegram.session_state", "revoking", now=stamp)
        legacy_conn.execute(
            "UPDATE security_state SET security_epoch = security_epoch + 1, updated_at = ? WHERE singleton_id = 1",
            (stamp,),
        )
    session.revoked = True
    crash("after_tx1")
    _event(writer, "started", "pending", load_security(legacy_conn)[0])
    crash("after_started")
    outcome = await send_logout_once(session, Deadline(_DEADLINE_S))
    crash("after_rpc")
    return await _finish(legacy_conn, writer, session, outcome, now)


async def finish_revocation(
    legacy_conn: Any, writer: AuditWriter, session: Any, *, now: datetime
) -> str | None:
    """Converge an interrupted revoke at startup; never sends ``auth.LogOut``."""
    state = get_setting(legacy_conn, "telegram.session_state")
    pending = _pending_started(writer.conn)
    if state == "revoking":
        session.revoked = True
        if pending is None:
            _event(writer, "started", "pending", load_security(legacy_conn)[0])
        return await _finish(legacy_conn, writer, session, "unknown", now)
    if pending is None:
        return None
    if state == "active":
        _event(writer, "finished", "aborted", int(pending["security_epoch"]))
        return "aborted"
    outcome = str(get_setting(legacy_conn, "telegram.remote_revoke"))
    _event(writer, "finished", outcome, load_security(legacy_conn)[0])
    return outcome
