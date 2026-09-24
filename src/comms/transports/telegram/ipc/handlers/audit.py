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

from comms.transports.telegram.authority.epochs import set_locked
from comms.transports.telegram.disclosure.audit.anchor import (
    AnchorError,
    derive_integrity,
    repair_anchor,
)
from comms.transports.telegram.disclosure.audit.chain import (
    APPEND_GUARD,
    ChainError,
    insert_checkpoint,
    verify_checkpoints_registry,
)
from comms.transports.telegram.ipc.handlers._wrapper import (
    AuditSink,
    Handler,
    TxCommand,
    admin_event,
    run_audited_tx,
)
from comms.transports.telegram.storage.authority_view import load_security
from comms.transports.telegram.storage.db import bind_epoch_state, write_epoch_state
from comms.transports.telegram.storage.settings import get_setting

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
