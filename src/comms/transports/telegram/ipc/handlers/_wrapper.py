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
from typing import Any

from comms.transports.telegram.disclosure.audit.anchor import (
    AnchorError,
    latch_degraded,
    write_anchor,
)
from comms.transports.telegram.disclosure.audit.chain import (
    ADMIN_EVENTS,
    APPEND_GUARD,
    EVENT_COLUMNS,
    append_event,
    mint_event_id,
)
from comms.transports.telegram.storage.settings import get_setting

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

Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class TxCommand[P, Q]:
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


def simulate_tx[T](
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
