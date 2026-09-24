"""The comms-db-key rekey protocol (comms v0.3 A14, N1): crash-safe at every boundary.

1. Stage the new key as version *n+1* in the secret store.
2. With **no transaction open** — checked here, since SQLCipher accepts ``PRAGMA rekey``
   inside ``BEGIN IMMEDIATE`` (measured, N1) — ``PRAGMA rekey``.
3. Close. 4. Reopen with the new key and read ``sqlite_master``.
5. Switch the file-backed pointer (atomic replace).
6. Destroy the old version.

Startup (``open_with_recovery``) tries the pointer's key, then every other stored version:
exactly one must open the file, and the pointer is repaired to it. Nothing is deleted
there; the next successful rekey removes every version but the one in use.
"""

from __future__ import annotations

import os
import secrets as _random
from pathlib import Path
from typing import Any

from comms.core.keys.secrets import SecretStore, SecretStoreError
from comms.core.storage.db import KEY_ERROR, CommsDbKeyError, io_guard, open_comms_db

__all__ = ["BOUNDARIES", "ITEM", "KeyPointer", "RekeyCrash", "open_with_recovery", "rekey"]

ITEM = "comms-db-key"
BOUNDARIES = ("after_stage", "after_rekey", "after_reopen", "after_pointer", "after_destroy")


class RekeyCrash(BaseException):
    """Raised only by the ``crash_at`` seam, which production never supplies."""


class KeyPointer:
    """The active comms-db-key version, in a 0600 file replaced atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get(self) -> int:
        try:
            text = self.path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            raise CommsDbKeyError(KEY_ERROR) from None
        if not text.isdigit() or int(text) < 1:
            raise CommsDbKeyError(KEY_ERROR)
        return int(text)

    def set(self, version: int) -> None:
        temp = self.path.with_name(f"{self.path.name}.{_random.token_hex(8)}")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, f"{int(version)}\n".encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _open(path: Path, secrets: SecretStore, version: int) -> Any | None:
    try:
        return open_comms_db(path, secrets.get(ITEM, version))
    except (CommsDbKeyError, SecretStoreError):
        return None


def open_with_recovery(path: Path, secrets: SecretStore, pointer: KeyPointer) -> Any:
    current = pointer.get()
    conn = _open(path, secrets, current)
    if conn is not None:
        return conn
    opened = [
        (v, c) for v in secrets.versions(ITEM) if v != current if (c := _open(path, secrets, v))
    ]
    if len(opened) != 1:
        for _version, other in opened:
            other.close()
        raise CommsDbKeyError(KEY_ERROR)
    version, conn = opened[0]
    pointer.set(version)
    return conn


def rekey(
    conn: Any, path: Path, secrets: SecretStore, pointer: KeyPointer, *, crash_at: str | None = None
) -> int:
    """Rekey the open ``conn`` (closed on success); return the new key version."""

    def crash(point: str) -> None:
        if crash_at == point:
            raise RekeyCrash(point)

    io_guard(conn)
    old = pointer.get()
    stored = secrets.versions(ITEM)
    for version in stored:
        if version != old:  # left by an interrupted run; the file opens under the pointer's key
            secrets.delete(ITEM, version)
    new_version = max([old, *stored]) + 1
    key = _random.token_bytes(32)
    secrets.put(ITEM, new_version, key)
    crash("after_stage")
    io_guard(conn)
    conn.execute(f"PRAGMA rekey = \"x'{key.hex()}'\"")
    crash("after_rekey")
    conn.close()
    open_comms_db(path, key).close()  # the verified reopen
    crash("after_reopen")
    pointer.set(new_version)
    crash("after_pointer")
    secrets.delete(ITEM, old)
    crash("after_destroy")
    return new_version
