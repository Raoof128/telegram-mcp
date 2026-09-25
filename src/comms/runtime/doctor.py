"""``comms doctor`` over the real state, read-only (D39-PRE Task E9).

It never writes: ``comms.db`` is opened with the pointer's key only (no pointer repair, no
migration, no latch, no re-anchor), ``meta.db`` read-only, and nothing is created when the state
is absent. It runs while a daemon runs. The report carries fixed codes and subjects (purposes,
phases), never material.

``ok`` is true when the only findings are ``CREDENTIAL_NOT_CONFIGURED`` (the owner's choice of
providers); ``--production`` exits nonzero otherwise.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from comms.core.doctor import Finding, doctor
from comms.core.keys.secrets import FileSecretStore, SecretStoreError
from comms.core.keys.slots import KeySlotStore
from comms.core.storage.db import CommsDbKeyError, open_comms_db
from comms.core.storage.migrations import MIGRATIONS
from comms.core.storage.rekey import ITEM, KeyPointer
from comms.runtime.paths import CommsPaths
from comms.runtime.state import bootstrap_state

__all__ = ["run_doctor"]

_ACCEPTABLE = frozenset({"CREDENTIAL_NOT_CONFIGURED"})


def _report(bootstrap: str, findings: list[Finding]) -> dict[str, Any]:
    return {
        "bootstrap": bootstrap,
        "findings": [{"code": f.code, "subject": f.subject, "detail": f.detail} for f in findings],
        "ok": all(f.code in _ACCEPTABLE for f in findings),
    }


def _legacy(paths: CommsPaths) -> tuple[Any, Any] | None:
    if not paths.legacy_db.exists():
        return None
    from comms.transports.telegram.disclosure.keys import checkpoint_public_for
    from comms.transports.telegram.keys.store import load_key, set_store_dir
    from comms.transports.telegram.runtime.cutover_barrier import legacy_verifier

    conn = sqlite3.connect(f"file:{paths.legacy_db}?mode=ro", uri=True)
    set_store_dir(paths.legacy_keys)
    return conn, legacy_verifier(load_key("audit-chain-key"), checkpoint_public_for(conn))


def run_doctor(paths: CommsPaths, *, now: datetime) -> dict[str, Any]:
    if not paths.db.exists() or not paths.db_key_pointer.exists():
        return _report(
            "UNINITIALIZED",
            [
                Finding(
                    "NOT_PROVISIONED", None, "comms is not provisioned (run: comms keys provision)"
                )
            ],
        )
    try:
        key = FileSecretStore(paths.secrets_dir).get(ITEM, KeyPointer(paths.db_key_pointer).get())
        conn = open_comms_db(paths.db, key)
    except (CommsDbKeyError, SecretStoreError):
        return _report(
            "UNINITIALIZED",
            [Finding("DB_KEY_INVALID", None, "the comms database key does not open comms.db")],
        )
    legacy = None
    try:
        row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
        if row is None or row[0] != MIGRATIONS[-1].version:
            return _report(
                "UNINITIALIZED",
                [Finding("SCHEMA_BEHIND", None, "comms.db migrates when the daemon starts")],
            )
        store = KeySlotStore(paths.slots_dir)
        legacy = _legacy(paths)
        findings = doctor(
            conn,
            store,
            now=now,
            legacy_conn=None if legacy is None else legacy[0],
            legacy=None if legacy is None else legacy[1],
        )
        return _report(bootstrap_state(conn, store, paths), findings)
    finally:
        conn.close()
        if legacy is not None:
            legacy[0].close()
