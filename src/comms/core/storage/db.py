"""Opening ``comms.db`` fail-closed, and the one write-transaction helper (design §2).

Measured on SQLCipher 4.12.0 community: ``PRAGMA key`` accepts a wrong key and
fails only at the first read, and a keyless open of a new file creates a
plaintext database. So the key is length-checked before ``connect``, proved by
a read before returning, and a fresh file's header is checked after its first
page is written.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlcipher3

__all__ = ["CommsDbKeyError", "TransactionIOError", "io_guard", "open_comms_db", "write_tx"]

_KEY_ERROR = "comms database key is invalid"
_PLAINTEXT_HEADER = b"SQLite format 3\x00"


class CommsDbKeyError(Exception):
    """The key is malformed or does not open this database. The message is fixed."""


class TransactionIOError(RuntimeError):
    """An external side effect was attempted while a comms.db transaction is open (R18)."""


def open_comms_db(path: Path, key: bytes) -> Any:
    """Open (or create) ``comms.db`` at ``path`` with a raw 256-bit key.

    ``key`` must be exactly 32 bytes of CSPRNG key material, supplied by the
    runtime (5e): never password-derived, never padded. It is used as a raw
    SQLCipher key (no KDF) and never appears in any error, repr or log.
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise CommsDbKeyError(_KEY_ERROR)
    fresh = not path.exists()
    conn = sqlcipher3.connect(str(path), isolation_level=None, timeout=5.0)
    try:
        conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        if not conn.execute("PRAGMA cipher_version").fetchone()[0]:
            raise CommsDbKeyError(_KEY_ERROR)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.dbapi2.DatabaseError:
            raise CommsDbKeyError(_KEY_ERROR) from None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA temp_store = MEMORY")
        if fresh:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            if path.read_bytes()[:16] == _PLAINTEXT_HEADER:
                conn.close()
                path.unlink()
                raise CommsDbKeyError(_KEY_ERROR)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def write_tx(conn: Any) -> Iterator[Any]:
    """``BEGIN IMMEDIATE`` … ``COMMIT``, or ``ROLLBACK`` on any exception. Never nested."""
    if conn.in_transaction:
        raise RuntimeError("nested comms.db transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def io_guard(conn: Any) -> None:
    """Refuse an external side effect while a transaction is open (R18)."""
    if conn.in_transaction:
        raise TransactionIOError("external I/O inside a comms.db transaction")
