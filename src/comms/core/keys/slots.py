"""Versioned, immutable key slots with one SQL-selected active version (design §B.4, A9).

Files live at ``<dir>/<purpose>/<version>``: the directory is 0700, each file is 0600 and
written once (``O_EXCL``), with the file and its directory fsynced. The SQL table
``key_slots`` owns which version is active; public halves of signing keys live in
``verification_keys``. Private material never enters the database.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core import timeutil
from comms.core.keys import ids
from comms.core.keys.purposes import PURPOSES
from comms.core.storage.db import write_tx

__all__ = [
    "KeySlotError",
    "KeySlotStore",
    "active_version",
    "bootstrap_comms_audit_keys",
    "key_id_for",
    "load_active",
    "load_version",
    "register_version",
    "registry_public_for",
]

_PURPOSE = re.compile(r"[a-z][a-z0-9-]{1,40}\Z")


class KeySlotError(Exception):
    """A key-slot operation was refused. Messages are fixed and never contain material."""


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class KeySlotStore:
    def __init__(self, directory: Path) -> None:
        directory = Path(directory)
        if directory.exists():
            if (
                stat.S_IMODE(directory.stat().st_mode) & 0o077
                or directory.stat().st_uid != os.getuid()
            ):
                raise KeySlotError("key slot directory permissions refused")
        else:
            directory.mkdir(mode=0o700, parents=True)
            os.chmod(directory, 0o700)
        self._dir = directory

    def __repr__(self) -> str:
        return "KeySlotStore(<redacted>)"

    def _purpose_dir(self, purpose: str) -> Path:
        if (
            not isinstance(purpose, str)
            or not _PURPOSE.fullmatch(purpose)
            or purpose not in PURPOSES
        ):
            raise KeySlotError("unknown key purpose")
        return self._dir / purpose

    def versions(self, purpose: str) -> list[int]:
        d = self._purpose_dir(purpose)
        if not d.exists():
            return []
        return sorted(int(p.name) for p in d.iterdir() if p.name.isdigit())

    def write_version(self, purpose: str, material: bytes) -> int:
        d = self._purpose_dir(purpose)
        if PURPOSES[purpose].rotation == "refused":
            raise KeySlotError("a retired key purpose is never minted")
        if not d.exists():
            d.mkdir(mode=0o700)
            os.chmod(d, 0o700)
            _fsync_dir(self._dir)
        version = (self.versions(purpose) or [0])[-1] + 1
        path = d / str(version)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, material)
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(d)
        return version

    def read(self, purpose: str, version: int) -> bytes:
        path = self._purpose_dir(purpose) / str(int(version))
        try:
            st = path.stat()
        except FileNotFoundError:
            raise KeySlotError("key slot missing") from None
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise KeySlotError("key slot permissions refused")
        return path.read_bytes()

    def destroy(self, purpose: str, version: int) -> None:
        path = self._purpose_dir(purpose) / str(int(version))
        try:
            path.unlink()
        except FileNotFoundError:
            return
        _fsync_dir(path.parent)


def _active_row(conn: Any, purpose: str) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT version, key_id FROM key_slots WHERE purpose = ? AND state = 'ACTIVE'", (purpose,)
    ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def active_version(conn: Any, purpose: str) -> tuple[int, str] | None:
    """The ACTIVE ``(version, key_id)`` of a purpose, or ``None``."""
    return _active_row(conn, purpose)


def _checked(store: KeySlotStore, purpose: str, version: int, stored_id: str) -> bytes:
    material = store.read(purpose, version)
    recomputed = (
        ids.ed25519_key_id(ids.ed25519_public(material))
        if stored_id.startswith("ed25519:")
        else ids.hmac_key_id(material)
    )
    if recomputed != stored_id:
        raise KeySlotError("key id mismatch")
    return material


def load_active(conn: Any, store: KeySlotStore, purpose: str) -> tuple[bytes, str]:
    """The active version's material and its recomputed, checked key ID."""
    row = _active_row(conn, purpose)
    if row is None:
        raise KeySlotError("no active key for purpose")
    version, stored_id = row
    return _checked(store, purpose, version, stored_id), stored_id


def load_version(conn: Any, store: KeySlotStore, purpose: str, version: int) -> bytes:
    """One registered, undestroyed version's material, its key ID recomputed and checked."""
    row = conn.execute(
        "SELECT key_id, state FROM key_slots WHERE purpose = ? AND version = ?",
        (purpose, int(version)),
    ).fetchone()
    if row is None or row[1] == "DESTROYED":
        raise KeySlotError("no such key version")
    return _checked(store, purpose, int(version), str(row[0]))


def registry_public_for(conn: Any) -> Callable[[str], bytes | None]:
    """A ``public_for`` over comms ``verification_keys``: raw public bytes by key ID."""

    def public_for(key_id: str) -> bytes | None:
        row = conn.execute(
            "SELECT public_key FROM verification_keys WHERE key_id = ?", (key_id,)
        ).fetchone()
        return None if row is None else bytes(row[0])

    return public_for


def key_id_for(purpose: str, material: bytes) -> str:
    """The key ID rule for a purpose's kind (one copy: ``comms.core.keys.ids``)."""
    kind = PURPOSES[purpose].kind
    if kind == "hmac":
        return ids.hmac_key_id(material)
    if kind == "ed25519":
        return ids.ed25519_key_id(ids.ed25519_public(material))
    raise KeySlotError("no key id rule for this key kind")


def register_version(conn: Any, purpose: str, version: int, material: bytes, stamp: str) -> str:
    """Record ``version`` as ACTIVE (and a signer's public half), in the caller's transaction."""
    key_id = key_id_for(purpose, material)
    conn.execute(
        "INSERT INTO key_slots (purpose, version, key_id, state, created_at)"
        " VALUES (?, ?, ?, 'ACTIVE', ?)",
        (purpose, version, key_id, stamp),
    )
    if PURPOSES[purpose].public_registry:
        conn.execute(
            "INSERT INTO verification_keys (key_id, purpose, algorithm, public_key,"
            " activated_at, trust_state) VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
            (key_id, purpose, PURPOSES[purpose].kind, ids.ed25519_public(material), stamp),
        )
    return key_id


def bootstrap_comms_audit_keys(conn: Any, store: KeySlotStore, *, now: datetime) -> None:
    """Provision audit-chain-key (HMAC) and audit-checkpoint-key (Ed25519) v1. Idempotent."""
    stamp = timeutil.iso(now)
    for purpose in ("audit-chain-key", "audit-checkpoint-key"):
        if _active_row(conn, purpose) is not None:
            continue
        material = secrets.token_bytes(32)
        version = store.write_version(purpose, material)
        with write_tx(conn):
            register_version(conn, purpose, version, material, stamp)
