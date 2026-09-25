"""Local MCP clients and their seeds, split daemon/helper (comms v0.3 Task D27; A33, A38).

``add_client`` mints a ``cli_`` ref and a 32-byte seed. The daemon keeps its verifier copy as
a ``cml1-client-seed`` key-slot version named by the client's row; the client's copy is written
**once**, to a 0600 file at a path the operator names, created exclusively (an existing file
is refused, never overwritten). ``rotate_client`` replaces both copies and destroys the old
version; ``disable_client`` refuses the client from then on. Seeds are never logged or shown.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core import refs, timeutil
from comms.core.keys.slots import KeySlotStore
from comms.core.storage.db import write_tx

__all__ = [
    "SEED_PURPOSE",
    "ClientError",
    "add_client",
    "client_seed",
    "disable_client",
    "rotate_client",
]

SEED_PURPOSE = "cml1-client-seed"
SEED_BYTES = 32
_NAME_MAX = 64


class ClientError(Exception):
    """A client operation was refused. Fixed messages, never a seed or a path's content."""


def _write_helper(path: Path, seed: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ClientError("the helper file already exists") from None
    except OSError:
        raise ClientError("the helper file cannot be written") from None
    try:
        os.write(fd, seed)
        os.fsync(fd)
    finally:
        os.close(fd)


def _row(conn: Any, cli: str) -> tuple[int, int, int]:
    try:
        refs.check(cli, "client")
    except ValueError:
        raise ClientError("unknown client") from None
    row = conn.execute(
        "SELECT id, enabled, seed_version FROM clients WHERE ref = ?", (cli,)
    ).fetchone()
    if row is None:
        raise ClientError("unknown client")
    return int(row[0]), int(row[1]), int(row[2])


def add_client(
    conn: Any, store: KeySlotStore, name: str, *, now: datetime, helper_path: Path
) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > _NAME_MAX:
        raise ClientError("a client name is required")
    seed = os.urandom(SEED_BYTES)
    _write_helper(Path(helper_path), seed)  # first: a failure leaves no client behind
    version = store.write_version(SEED_PURPOSE, seed)
    cli = refs.mint("client")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO clients (ref, name, seed_version, created_at) VALUES (?, ?, ?, ?)",
            (cli, name, version, timeutil.iso(now)),
        )
    return cli


def rotate_client(
    conn: Any, store: KeySlotStore, cli: str, *, now: datetime, helper_path: Path
) -> None:
    """Replace both copies of the client's seed; the old one verifies nothing afterwards."""
    row_id, enabled, old = _row(conn, cli)
    if not enabled:
        raise ClientError("the client is disabled")
    seed = os.urandom(SEED_BYTES)
    _write_helper(Path(helper_path), seed)
    version = store.write_version(SEED_PURPOSE, seed)
    with write_tx(conn):
        conn.execute(
            "UPDATE clients SET seed_version = ?, rotated_at = ? WHERE id = ?",
            (version, timeutil.iso(now), row_id),
        )
    store.destroy(SEED_PURPOSE, old)  # at_rotation (A38)


def disable_client(conn: Any, cli: str) -> None:
    row_id, _enabled, _version = _row(conn, cli)
    with write_tx(conn):
        conn.execute("UPDATE clients SET enabled = 0 WHERE id = ?", (row_id,))


def client_seed(conn: Any, store: KeySlotStore, cli: str) -> bytes | None:
    """The daemon's copy of an enabled client's current seed, or None."""
    try:
        _row_id, enabled, version = _row(conn, cli)
    except ClientError:
        return None
    if not enabled:
        return None
    try:
        return store.read(SEED_PURPOSE, version)
    except Exception:  # noqa: BLE001 -- a missing or unreadable seed verifies nothing
        return None
