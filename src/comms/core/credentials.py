"""Provider credentials rotate stage → prove → activate → re-check (comms v0.3 A13, O2).

1. The candidate is written as a new secret version.
2. ``prove`` — a live identity or capability check the adapter supplies — must pass;
   otherwise the candidate is destroyed and the working credential stays active.
3. One audited transaction switches the SQL pointer (``key_slots``) and appends
   ``admin.credential_rotation``; the old version is kept.
4. ``recheck`` (``prove`` again by default) runs against the now-active credential.
5. Only then is the old version destroyed. If the re-check fails, a second audited
   transaction restores the old version, marks the candidate ``ORPHAN`` and appends
   ``admin.credential_rotation_rolled_back``; ``CredentialCheckFailed`` is raised.

Values never reach an event, a log line, an error or ``key_slots`` (which holds only the
credential's ID, a hash). ``revoke_credential`` destroys the active version: the adapter
then reports ``NOT_CONFIGURED``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.audit.integrity import require_not_degraded
from comms.core.audit.writer import AuditWriter
from comms.core.keys.purposes import PURPOSES
from comms.core.keys.secrets import SecretStore
from comms.core.keys.slots import KeySlotError, active_version, key_id_for, register_version
from comms.core.storage.db import write_tx

__all__ = [
    "CredentialCheckFailed",
    "active_credential",
    "credential_status",
    "revoke_credential",
    "rotate_credential",
]


class CredentialCheckFailed(Exception):
    """The candidate did not prove itself, before or after activation. Fixed message."""

    def __init__(self) -> None:
        super().__init__("the credential did not pass its live check")


def _credential(purpose: str) -> None:
    spec = PURPOSES.get(purpose)
    if spec is None or spec.rotation != "staged":
        raise KeySlotError("not a provider credential")


def _passes(check: Callable[[bytes], Any], value: bytes) -> bool:
    try:
        check(value)
    except Exception:  # noqa: BLE001 -- any failure of the live check is a failed proof
        return False
    return True


def credential_status(conn: Any, purpose: str) -> str:
    _credential(purpose)
    return "CONFIGURED" if active_version(conn, purpose) else "NOT_CONFIGURED"


def active_credential(conn: Any, secrets: SecretStore, purpose: str) -> bytes | None:
    """The active credential, its ID recomputed; ``None`` when not configured."""
    _credential(purpose)
    row = active_version(conn, purpose)
    if row is None:
        return None
    value = secrets.get(purpose, row[0])
    if key_id_for(purpose, value) != row[1]:
        raise KeySlotError("credential id mismatch")
    return value


def _set_state(conn: Any, purpose: str, version: int, state: str) -> None:
    conn.execute(
        "UPDATE key_slots SET state = ? WHERE purpose = ? AND version = ?",
        (state, purpose, version),
    )


def rotate_credential(
    writer: AuditWriter,
    secrets: SecretStore,
    purpose: str,
    value: bytes,
    *,
    prove: Callable[[bytes], Any],
    recheck: Callable[[bytes], Any] | None = None,
    now: datetime,
) -> int:
    _credential(purpose)
    conn = writer.conn
    require_not_degraded(conn)
    old = active_version(conn, purpose)
    version = (
        max(
            [
                0,
                *secrets.versions(purpose),
                *(
                    r[0]
                    for r in conn.execute(
                        "SELECT version FROM key_slots WHERE purpose = ?", (purpose,)
                    )
                ),
            ]
        )
        + 1
    )
    secrets.put(purpose, version, value)
    if not _passes(prove, value):
        secrets.delete(purpose, version)
        raise CredentialCheckFailed
    stamp = timeutil.iso(now)
    with writer.transaction() as tx:
        if old is not None:
            _set_state(conn, purpose, old[0], "RETIRED")
        register_version(conn, purpose, version, value, stamp)
        tx.append(
            "admin.credential_rotation",
            payload={
                "purpose": purpose,
                "old_version": old[0] if old else 0,
                "new_version": version,
            },
        )
    if not _passes(recheck or prove, value):
        with writer.transaction() as tx:
            _set_state(conn, purpose, version, "ORPHAN")
            if old is not None:
                _set_state(conn, purpose, old[0], "ACTIVE")
            tx.append(
                "admin.credential_rotation_rolled_back",
                payload={
                    "purpose": purpose,
                    "restored_version": old[0] if old else 0,
                    "orphaned_version": version,
                },
            )
        raise CredentialCheckFailed
    if old is not None:
        secrets.delete(purpose, old[0])
        with write_tx(conn):
            _set_state(conn, purpose, old[0], "DESTROYED")
    return version


def revoke_credential(
    writer: AuditWriter, secrets: SecretStore, purpose: str, *, now: datetime
) -> None:
    _credential(purpose)
    conn = writer.conn
    row = active_version(conn, purpose)
    if row is None:
        raise KeySlotError("no active credential")
    with writer.transaction() as tx:
        _set_state(conn, purpose, row[0], "REVOKED")
        tx.append("admin.credential_revoked", payload={"purpose": purpose, "version": row[0]})
    secrets.delete(purpose, row[0])
    with write_tx(conn):
        _set_state(conn, purpose, row[0], "DESTROYED")
