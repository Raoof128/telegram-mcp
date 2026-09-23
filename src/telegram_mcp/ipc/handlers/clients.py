"""``client rotate`` and ``client list`` (spec §33, §9.7): coding clients only."""

from __future__ import annotations

import os
import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telegram_mcp.disclosure.audit.chain import immediate_transaction
from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["client_handlers"]

_KINDS = ("codex_local", "claude_code_local")


def _write_seed(key_dir: Path, client_ref: str) -> None:
    path = key_dir / f"lease-seed.{client_ref}"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secrets.token_bytes(32))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def client_handlers(
    conn: sqlite3.Connection, *, key_dir: Path
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def rotate(args: dict[str, Any]) -> dict[str, Any]:
        kind = args.get("client")
        if kind not in _KINDS:
            raise ValueError("client must be codex_local or claude_code_local")
        principal = conn.execute("SELECT id FROM principals ORDER BY id LIMIT 1").fetchone()
        if principal is None:
            raise ValueError("the owner principal does not exist")
        row = conn.execute(
            "SELECT client_ref FROM mcp_clients WHERE client_kind = ?", (kind,)
        ).fetchone()
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with immediate_transaction(conn):
            if row is None:
                ref = mint_opaque_ref("tcl_")
                conn.execute(
                    "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding,"
                    " client_kind, enabled, created_at) VALUES (?, ?, 'bearer', ?, ?, 1, ?)",
                    (principal[0], ref, f"lease-seed:{ref}", kind, now),
                )
            else:
                ref = row[0]
                conn.execute(
                    "UPDATE mcp_clients SET rotated_at = ?, enabled = 1 WHERE client_ref = ?",
                    (now, ref),
                )
            _write_seed(key_dir, ref)
        return {"client_ref": ref}

    def list_(args: dict[str, Any]) -> dict[str, Any]:
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
