"""Database open path, integrity gates, GC, and the Task-6 bindings.

Startup order follows the design's normative readiness list, steps 5-9:
open SQLite, ``foreign_keys=ON`` *verified* (spec §12.1 requires the pragma
to be executed and verified on every connection), ``quick_check``, then
migrations, then GC. Any failure raises :class:`StorageError` and leaves
nothing open.

Permissions (spec §12.4): the metadata directory is ``0700``, and the
database plus its journal/WAL/SHM sidecars are ``0600``, re-asserted and
verified after the WAL files come into existence. The path itself may not be
a symlink: a symlinked database would let anything that can write the link
redirect the daemon's writes.

Startup GC deletes **every** cursor row, not only expired ones. A cursor is
bound to the ``runtime_id`` of the runtime that minted it (design §5), and a
freshly started runtime has minted none, so every row present at startup
belongs to a previous runtime and can never be presented again. The hourly
and shutdown passes use :func:`purge_expired_cursors`, which drops only
rows past ``expires_at``, so a live cursor is never deleted early
(spec §12.2 cursors note).

``expires_at`` comparisons happen in SQL so the required
``cursors(expires_at)`` index is used. That makes the comparison
lexicographic, which is only correct because every cursor row is written by
:class:`SqliteCursorStore` through one canonical RFC 3339 UTC writer.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telegram_mcp.authority.cursors import CursorRecord
from telegram_mcp.authority.epochs import EpochState
from telegram_mcp.storage.migrations import migrate

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "SqliteCursorStore",
    "StorageError",
    "bind_cursor_store",
    "bind_epoch_state",
    "open_db",
    "purge_expired_cursors",
    "require_foreign_keys",
    "save_epoch_state",
    "startup_gc",
    "write_epoch_state",
]

ERR_SYMLINK = "database path must not be a symlink"
ERR_DIR_MODE = "database directory must be mode 0700 owned by the current user"
ERR_FILE_MODE = "database files must be mode 0600"
ERR_FOREIGN_KEYS = "connection must enforce PRAGMA foreign_keys=ON"
ERR_INTEGRITY = "database failed its integrity check"
ERR_OPEN = "database could not be opened"
ERR_UNKNOWN_REF = "unknown reference"

_SIDECARS = ("", "-wal", "-shm")
_ISO_MICROS = "%Y-%m-%dT%H:%M:%S.%fZ"


class StorageError(Exception):
    """Any storage-layer refusal. Fixed message; never carries row data."""


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(float(epoch_seconds), UTC).strftime(_ISO_MICROS)


def _epoch(text: str) -> float:
    return datetime.strptime(text, _ISO_MICROS).replace(tzinfo=UTC).timestamp()


def _now_iso() -> str:
    return datetime.now(UTC).strftime(_ISO_MICROS)


def _ensure_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
        os.chmod(path, 0o700)  # mkdir mode is umask-masked; pin ours exactly
    if path.is_symlink() or not path.is_dir():
        raise StorageError(ERR_DIR_MODE)
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
        raise StorageError(ERR_DIR_MODE)


def _pin_file_modes(target: Path) -> None:
    for suffix in _SIDECARS:
        sidecar = target.with_name(target.name + suffix)
        if not sidecar.exists():
            continue
        if sidecar.is_symlink():
            raise StorageError(ERR_SYMLINK)
        os.chmod(sidecar, 0o600)
        if stat.S_IMODE(sidecar.stat().st_mode) != 0o600:
            raise StorageError(ERR_FILE_MODE)


def require_foreign_keys(conn: sqlite3.Connection) -> None:
    """Verify the pragma actually took effect on this connection."""
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    if not row or int(row[0]) != 1:
        raise StorageError(ERR_FOREIGN_KEYS)


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open the metadata database through every startup gate."""
    target = Path(path)
    if target.is_symlink():
        raise StorageError(ERR_SYMLINK)
    _ensure_dir(target.parent)
    try:
        conn = sqlite3.connect(target)
    except sqlite3.Error as exc:
        raise StorageError(ERR_OPEN) from exc
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        require_foreign_keys(conn)
        conn.execute("PRAGMA journal_mode = WAL")
        check = conn.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise StorageError(ERR_INTEGRITY)
        migrate(conn)
        startup_gc(conn)
        _pin_file_modes(target)
    except StorageError:
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise StorageError(ERR_INTEGRITY) from exc
    return conn


def startup_gc(conn: sqlite3.Connection) -> int:
    """Drop every cursor row: all of them predate this runtime."""
    deleted = conn.execute("DELETE FROM cursors").rowcount
    conn.commit()
    return int(deleted)


def purge_expired_cursors(conn: sqlite3.Connection, *, now: str | float | None = None) -> int:
    """Drop only cursor rows past ``expires_at`` (hourly and at shutdown)."""
    stamp = _now_iso() if now is None else (now if isinstance(now, str) else _iso(now))
    deleted = conn.execute("DELETE FROM cursors WHERE expires_at < ?", (stamp,)).rowcount
    conn.commit()
    return int(deleted)


def _lookup_id(conn: sqlite3.Connection, table: str, column: str, ref: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE {column} = ?", (ref,)).fetchone()
    if row is None:
        raise StorageError(ERR_UNKNOWN_REF)
    return int(row[0])


class SqliteCursorStore:
    """``CursorStore`` bound to the ``cursors`` table.

    The record's opaque refs are resolved to row ids on write and back to
    refs on read. ``runtime_id`` has no spec column, so it travels inside
    ``state_json`` under the reserved ``rt`` key alongside the bounded
    pagination state — no Telegram content ever enters either.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        require_foreign_keys(conn)
        self._conn = conn

    def put(self, ref: str, record: CursorRecord) -> None:
        conn = self._conn
        conn.execute(
            "INSERT INTO cursors(cursor_ref, principal_id, client_id, account_id,"
            " security_epoch, policy_epoch, project_scope_digest, tool_name, query_digest,"
            " state_json, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ref,
                _lookup_id(conn, "principals", "principal_ref", record.principal),
                _lookup_id(conn, "mcp_clients", "client_ref", record.client),
                _lookup_id(conn, "accounts", "account_ref", record.account),
                record.security_epoch,
                record.policy_epoch,
                record.project_scope_digest,
                record.tool,
                record.query_digest,
                json.dumps(
                    {"rt": record.runtime_id, "state": record.state},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                _iso(record.created_at),
                _iso(record.expires_at),
            ),
        )
        conn.commit()

    def get(self, ref: str) -> CursorRecord | None:
        row = self._conn.execute(
            "SELECT p.principal_ref, c.client_ref, a.account_ref, cu.tool_name, cu.query_digest,"
            " cu.project_scope_digest, cu.policy_epoch, cu.security_epoch, cu.state_json,"
            " cu.created_at, cu.expires_at"
            " FROM cursors cu"
            " JOIN principals p ON p.id = cu.principal_id"
            " JOIN mcp_clients c ON c.id = cu.client_id"
            " JOIN accounts a ON a.id = cu.account_id"
            " WHERE cu.cursor_ref = ?",
            (ref,),
        ).fetchone()
        if row is None:
            return None
        blob = json.loads(row[8])
        return CursorRecord(
            principal=row[0],
            client=row[1],
            account=row[2],
            tool=row[3],
            query_digest=row[4],
            project_scope_digest=row[5],
            policy_epoch=int(row[6]),
            security_epoch=int(row[7]),
            runtime_id=str(blob["rt"]),
            created_at=_epoch(row[9]),
            expires_at=_epoch(row[10]),
            state=dict(blob["state"]),
        )

    def delete(self, ref: str) -> bool:
        deleted = self._conn.execute("DELETE FROM cursors WHERE cursor_ref = ?", (ref,)).rowcount
        self._conn.commit()
        return int(deleted) > 0

    def purge_expired(self, now: float) -> int:
        return purge_expired_cursors(self._conn, now=now)


def bind_cursor_store(conn: sqlite3.Connection) -> SqliteCursorStore:
    """Bind the Task-6 ``CursorStore`` protocol to this connection."""
    return SqliteCursorStore(conn)


def bind_epoch_state(conn: sqlite3.Connection) -> EpochState:
    """Load ``security_state``, ``policy_state`` and project epochs as state.

    The returned dict is exactly the shape :mod:`telegram_mcp.authority.epochs`
    mutates; :func:`save_epoch_state` writes it back in one transaction.
    """
    require_foreign_keys(conn)
    security = conn.execute(
        "SELECT singleton_id, security_epoch, locked, locked_at, updated_at"
        " FROM security_state WHERE singleton_id = 1"
    ).fetchone()
    if security is None:
        raise StorageError("security_state singleton is missing")
    policy: dict[tuple[str, str], dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT p.principal_ref, a.account_ref, ps.policy_epoch, ps.updated_at"
        " FROM policy_state ps"
        " JOIN principals p ON p.id = ps.principal_id"
        " JOIN accounts a ON a.id = ps.account_id"
    ):
        policy[(row[0], row[1])] = {"policy_epoch": int(row[2]), "updated_at": row[3]}
    projects = {
        row[0]: {"project_epoch": int(row[1]), "updated_at": row[2]}
        for row in conn.execute("SELECT project_ref, project_epoch, updated_at FROM projects")
    }
    return {
        "security_state": {
            "singleton_id": int(security[0]),
            "security_epoch": int(security[1]),
            "locked": int(security[2]),
            "locked_at": security[3],
            "updated_at": security[4],
        },
        "policy_state": policy,
        "projects": projects,
    }


def write_epoch_state(conn: sqlite3.Connection, state: Mapping[str, Any]) -> None:
    """The UPDATEs of :func:`save_epoch_state`, inside the caller's transaction."""
    require_foreign_keys(conn)
    if not conn.in_transaction:
        raise StorageError("write_epoch_state requires an open transaction")
    security = state["security_state"]
    conn.execute(
        "UPDATE security_state SET security_epoch = ?, locked = ?, locked_at = ?,"
        " updated_at = ? WHERE singleton_id = 1",
        (
            int(security["security_epoch"]),
            int(security["locked"]),
            security["locked_at"],
            security["updated_at"],
        ),
    )
    for (principal_ref, account_ref), row in state["policy_state"].items():
        conn.execute(
            "UPDATE policy_state SET policy_epoch = ?, updated_at = ?"
            " WHERE principal_id = (SELECT id FROM principals WHERE principal_ref = ?)"
            " AND account_id = (SELECT id FROM accounts WHERE account_ref = ?)",
            (int(row["policy_epoch"]), row["updated_at"], principal_ref, account_ref),
        )
    for project_ref, row in state["projects"].items():
        conn.execute(
            "UPDATE projects SET project_epoch = ?, updated_at = ? WHERE project_ref = ?",
            (int(row["project_epoch"]), row["updated_at"], project_ref),
        )


def save_epoch_state(conn: sqlite3.Connection, state: Mapping[str, Any]) -> None:
    """Write epoch state back transactionally (spec §10.6, §10.8, §12.2)."""
    conn.commit()
    try:
        conn.execute("BEGIN")
        write_epoch_state(conn, state)
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
