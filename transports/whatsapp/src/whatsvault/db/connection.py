"""Keyed SQLCipher connections. PRAGMA key is lazy — a wrong key does not fail
until a page is read — so open_db performs an eager read to validate the key
before returning (spec §2.4). Only real controls are applied: no
`cipher_secure_delete` (that pragma does not exist and silently no-ops)."""

import sqlcipher3

from whatsvault.crypto.keystore import KeyStore

DB_KEY_NAMES = {
    "vault": "whatsvault.vault.key.v1",
    "control": "whatsvault.control.key.v1",
}


def open_db(path: str, key: bytes, *, check_same_thread: bool = True):
    """Open a keyed connection.

    check_same_thread=False is for a server that dispatches handlers onto a
    worker thread (the MCP daemon does). It removes the DB-API's thread check,
    NOT the need to serialise: the caller must hold a lock around every use, or
    two requests will interleave on one connection. apps.mcp.server does both.
    """
    conn = sqlcipher3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def provision_db(kind: str, path: str, ks: KeyStore, *, check_same_thread: bool = True):
    if kind not in DB_KEY_NAMES:
        raise ValueError(f"unknown db kind: {kind!r}")
    key = ks.provision(DB_KEY_NAMES[kind], 32)
    return open_db(path, key, check_same_thread=check_same_thread)


def open_existing(kind: str, path: str, ks: KeyStore, *, check_same_thread: bool = True):
    if kind not in DB_KEY_NAMES:
        raise ValueError(f"unknown db kind: {kind!r}")
    key = ks.require(DB_KEY_NAMES[kind], 32)
    return open_db(path, key, check_same_thread=check_same_thread)
