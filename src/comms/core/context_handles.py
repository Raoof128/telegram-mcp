"""ctx_ handles and cur_ cursors: their rows (comms v0.3 Task D9; A30, G13).

The service (``services/handles.py``) decides validity; this module only stores and loads.
A cursor-key rotation deletes every row (``keys/rotate.py``, B7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from comms.core import refs
from comms.core.storage.db import write_tx

__all__ = ["CursorRow", "HandleRow", "insert_cursor", "insert_handle", "load_cursor", "load_handle"]


@dataclass(frozen=True)
class HandleRow:
    ref: str
    client: str
    owner: str
    security_epoch: int
    query_digest: str
    target_ref: str
    actor: str
    snapshot: dict[str, Any]
    expires_at: str


@dataclass(frozen=True)
class CursorRow:
    ref: str
    ctx_ref: str
    position: dict[str, Any]
    key_version: int
    expires_at: str


def insert_handle(
    conn: Any,
    *,
    client: str,
    owner: str,
    epoch: int,
    query_digest: str,
    target_ref: str,
    actor: str,
    snapshot: dict[str, Any],
    created_at: str,
    expires_at: str,
) -> str:
    ref = refs.mint("context")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO ctx_handles (ref, client, owner, security_epoch, query_digest, target_ref, actor,"
            " snapshot, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ref,
                client,
                owner,
                epoch,
                query_digest,
                target_ref,
                actor,
                json.dumps(snapshot, sort_keys=True),
                created_at,
                expires_at,
            ),
        )
    return ref


def load_handle(conn: Any, ref: str) -> HandleRow | None:
    row = conn.execute(
        "SELECT ref, client, owner, security_epoch, query_digest, target_ref, actor, snapshot, expires_at"
        " FROM ctx_handles WHERE ref = ?",
        (ref,),
    ).fetchone()
    if row is None:
        return None
    return HandleRow(
        row[0], row[1], row[2], int(row[3]), row[4], row[5], row[6], json.loads(row[7]), row[8]
    )


def insert_cursor(
    conn: Any,
    *,
    ctx_ref: str,
    position: dict[str, Any],
    key_version: int,
    created_at: str,
    expires_at: str,
) -> str:
    ref = refs.mint("cursor")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO cursors (ref, ctx_ref, position, cursor_key_version, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                ref,
                ctx_ref,
                json.dumps(position, sort_keys=True),
                key_version,
                created_at,
                expires_at,
            ),
        )
    return ref


def load_cursor(conn: Any, ref: str) -> CursorRow | None:
    row = conn.execute(
        "SELECT ref, ctx_ref, position, cursor_key_version, expires_at FROM cursors WHERE ref = ?",
        (ref,),
    ).fetchone()
    if row is None:
        return None
    return CursorRow(row[0], row[1], json.loads(row[2]), int(row[3]), row[4])
