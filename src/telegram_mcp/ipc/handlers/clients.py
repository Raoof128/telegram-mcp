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

from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, run_tx, tx_handler
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

    return {
        "client rotate": rotate,
        "client list": list_,
        "client disable": tx_handler(conn, CLIENT_COMMANDS["client disable"]),
    }
