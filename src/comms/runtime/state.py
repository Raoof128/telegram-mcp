"""``open_comms_state``: the daemon's fail-closed open of comms (D39-PRE Task E2).

Order, each step fail-closed with a fixed message that names the fix:

1. the state is provisioned (pointer and database present);
2. the key opens the database (``open_with_recovery`` repairs a pointer a crashed rekey left);
3. the schema migrates;
4. SQLCipher's and SQLite's integrity checks pass;
5. every required key loads and its id recomputes;
6. the audit anchor agrees with the comms chain.

Step 6 depends on the bootstrap state (owner amendment). ``PROVISIONED`` (no chain, no anchor)
is expected before the cutover's genesis. Once the chain exists, a clean anchor is ``READY``; an
anchor exactly one event behind (a crash between a commit and its refresh) is re-anchored; any
other disagreement starts the daemon degraded: reads work, new writes are refused.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from comms.core.audit.anchor import CLEAN, COMMS_ANCHOR, RECOVERY_REQUIRED, derive_integrity
from comms.core.audit.chain import COMMS, head
from comms.core.audit.integrity import latch_degraded
from comms.core.audit.writer import AuditWriter, SlotChainKeys
from comms.core.doctor import REQUIRED_KEYS
from comms.core.keys.secrets import FileSecretStore, SecretStoreError
from comms.core.keys.slots import KeySlotError, KeySlotStore, load_active, registry_public_for
from comms.core.storage.db import CommsDbKeyError
from comms.core.storage.migrations import migrate
from comms.core.storage.rekey import KeyPointer, open_with_recovery
from comms.runtime.paths import CommsPaths

__all__ = ["BootstrapState", "CommsState", "StateRefused", "bootstrap_state", "open_comms_state"]

BootstrapState = Literal["PROVISIONED", "CHAIN_INITIALIZED", "READY"]


class StateRefused(Exception):
    """The comms state cannot be opened; the message is fixed and names the fix."""


@dataclass(frozen=True)
class CommsState:
    conn: Any
    secrets: FileSecretStore
    store: KeySlotStore
    writer: AuditWriter
    paths: CommsPaths

    def __repr__(self) -> str:
        return "CommsState(<redacted>)"


def _integrity(conn: Any, store: KeySlotStore, paths: CommsPaths) -> str:
    return derive_integrity(
        conn,
        COMMS,
        COMMS_ANCHOR,
        SlotChainKeys(conn, store).for_epoch,
        paths.anchor,
        public_for=registry_public_for(conn),
    )


def bootstrap_state(conn: Any, store: KeySlotStore, paths: CommsPaths) -> BootstrapState:
    if head(conn, COMMS) is None:
        return "PROVISIONED"
    return "READY" if _integrity(conn, store, paths) == CLEAN else "CHAIN_INITIALIZED"


def _checks_pass(conn: Any) -> bool:
    cipher = conn.execute("PRAGMA cipher_integrity_check").fetchall()
    plain = conn.execute("PRAGMA integrity_check").fetchall()
    return not cipher and [tuple(r) for r in plain] == [("ok",)]


def open_comms_state(paths: CommsPaths, *, clock: Callable[[], datetime]) -> CommsState:
    if not paths.db.exists() or not paths.db_key_pointer.exists():
        raise StateRefused("comms is not provisioned (run: comms keys provision)")
    secrets, store = FileSecretStore(paths.secrets_dir), KeySlotStore(paths.slots_dir)
    try:
        conn = open_with_recovery(paths.db, secrets, KeyPointer(paths.db_key_pointer))
    except (CommsDbKeyError, SecretStoreError):
        raise StateRefused("the comms database key does not open comms.db") from None
    try:
        try:
            migrate(conn)
        except Exception:  # noqa: BLE001 -- any migration failure refuses the start
            raise StateRefused("the comms database could not be migrated") from None
        if not _checks_pass(conn):
            raise StateRefused("the comms database failed its integrity check")
        try:
            for purpose in REQUIRED_KEYS:
                load_active(conn, store, purpose)
        except KeySlotError:
            raise StateRefused(
                "a required comms key is missing or corrupt (run: comms doctor)"
            ) from None
        writer = AuditWriter(conn, SlotChainKeys(conn, store), paths.anchor, clock=clock)
        if head(conn, COMMS) is not None:
            integrity = _integrity(conn, store, paths)
            if integrity == RECOVERY_REQUIRED:
                writer.refresh_anchor()  # a crash between a commit and its anchor refresh
            elif integrity != CLEAN:
                latch_degraded(conn, reason="ANCHOR_MISMATCH_AT_START", now=clock())
    except BaseException:
        conn.close()
        raise
    return CommsState(conn=conn, secrets=secrets, store=store, writer=writer, paths=paths)
