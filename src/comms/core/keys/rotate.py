"""The generic key rotation protocol: stage → prove → activate (comms v0.3 design §B.4, A9).

1. The new material is written to a fresh immutable slot (file + fsync).
2. ``prove(material)`` must succeed; if it raises, the slot is left as an orphan.
3. One audited transaction retires the old version, registers the new one as ACTIVE (a
   signer's old public half becomes ``TRUSTED_RETIRED``), applies the purpose's
   ``consequence``, and appends ``admin.key_rotation`` unless the consequence appended its
   own event. The writer anchors exactly that head.
4. The new version is reloaded and checked, then the old private material is destroyed if
   the purpose's rule says so (``at_rotation``, ``at_once``).

Rotating ``audit-chain-key`` (``seal_epoch``, G8) seals the current epoch: the rotation event
is the next epoch's first event, MACed under the new key, so epoch ``n`` is always MACed
under key version ``n``. The old secret stays until its epoch is truncated.

A crash before step 3 leaves an orphan (reported by ``find_orphans``, cleaned by the next
rotation of that purpose); a crash after it leaves the new version selected. Rotation is
refused while the audit integrity latch is set. ``crash_at`` is a test seam that
production never supplies.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.audit.chain import COMMS, head
from comms.core.audit.integrity import require_not_degraded
from comms.core.audit.writer import AuditTx, AuditWriter
from comms.core.keys.purposes import PURPOSES
from comms.core.keys.slots import (
    KeySlotError,
    KeySlotStore,
    active_version,
    key_id_for,
    load_active,
    register_version,
)
from comms.core.storage.db import write_tx

__all__ = ["RotationCrash", "clean_orphans", "find_orphans", "rotate"]

_GENERIC_ROTATIONS = frozenset({"new_id", "invalidate", "seal_epoch"})
_GENERIC_KINDS = frozenset({"hmac", "ed25519"})
_DESTROY_AT_ROTATION = frozenset({"at_rotation", "at_once"})


class RotationCrash(BaseException):
    """Raised only by the ``crash_at`` seam, which production never supplies."""


def _registered(conn: Any, purpose: str) -> set[int]:
    return {
        int(r[0])
        for r in conn.execute("SELECT version FROM key_slots WHERE purpose = ?", (purpose,))
    }


def find_orphans(conn: Any, store: KeySlotStore) -> dict[str, list[int]]:
    """Staged slots with no registered version, per purpose (what ``doctor`` reports)."""
    found = {}
    for purpose in sorted(PURPOSES):
        orphans = sorted(set(store.versions(purpose)) - _registered(conn, purpose))
        if orphans:
            found[purpose] = orphans
    return found


def clean_orphans(conn: Any, store: KeySlotStore, purpose: str) -> list[int]:
    orphans = sorted(set(store.versions(purpose)) - _registered(conn, purpose))
    for version in orphans:
        store.destroy(purpose, version)
    return orphans


def _open_epoch(
    conn: Any, store: KeySlotStore, purpose: str, material: bytes
) -> Callable[[AuditTx, int, int], bool]:
    """The ``seal_epoch`` consequence (G8): the rotation event is the new epoch's first event."""
    checkpoint_key = load_active(conn, store, "audit-checkpoint-key")[0]

    def consequence(tx: AuditTx, old_version: int, new_version: int) -> bool:
        current = head(tx.conn, COMMS)
        if (
            current is None
            or current["chain_epoch"] != old_version
            or new_version != old_version + 1
        ):
            raise KeySlotError("chain key version and chain epoch diverged")
        tx.open_epoch(
            "admin.key_rotation",
            new_key=material,
            checkpoint_key=checkpoint_key,
            payload={
                "purpose": purpose,
                "old_version": old_version,
                "new_version": new_version,
                "key_id": key_id_for(purpose, material),
            },
        )
        return True

    return consequence


def rotate(
    writer: AuditWriter,
    store: KeySlotStore,
    purpose: str,
    *,
    material: bytes,
    prove: Callable[[bytes], None],
    consequence: Callable[[AuditTx, int, int], bool] | None = None,
    now: datetime,
    crash_at: str | None = None,
) -> int:
    spec = PURPOSES.get(purpose)
    if spec is None:
        raise KeySlotError("unknown key purpose")
    if spec.rotation == "refused":
        raise KeySlotError("a retired key purpose is never rotated")
    if spec.rotation not in _GENERIC_ROTATIONS or spec.kind not in _GENERIC_KINDS:
        raise KeySlotError("this purpose has its own rotation protocol")
    if spec.rotation == "seal_epoch" and consequence is not None:
        raise KeySlotError("an epoch-sealing rotation takes no other consequence")
    conn = writer.conn
    require_not_degraded(conn)
    clean_orphans(conn, store, purpose)
    old = active_version(conn, purpose)
    old_version = old[0] if old else 0
    version = store.write_version(purpose, material)
    prove(material)
    if crash_at == "before_tx":
        raise RotationCrash(crash_at)
    stamp = timeutil.iso(now)
    if spec.rotation == "seal_epoch":
        consequence = _open_epoch(conn, store, purpose, material)
    with writer.transaction() as tx:
        if old is not None:
            conn.execute(
                "UPDATE key_slots SET state = 'RETIRED', retired_at = ? WHERE purpose = ? AND version = ?",
                (stamp, purpose, old_version),
            )
            if spec.public_registry:
                conn.execute(
                    "UPDATE verification_keys SET trust_state = 'TRUSTED_RETIRED', retired_at = ?"
                    " WHERE key_id = ?",
                    (stamp, old[1]),
                )
        key_id = register_version(conn, purpose, version, material, stamp)
        if consequence is None or not consequence(tx, old_version, version):
            tx.append(
                "admin.key_rotation",
                payload={
                    "purpose": purpose,
                    "old_version": old_version,
                    "new_version": version,
                    "key_id": key_id,
                },
            )
    if crash_at == "after_tx":
        raise RotationCrash(crash_at)
    if load_active(conn, store, purpose)[1] != key_id_for(purpose, material):
        raise KeySlotError("rotation did not take effect")
    if old is not None and spec.private_destroy in _DESTROY_AT_ROTATION:
        store.destroy(purpose, old_version)
        with write_tx(conn):
            conn.execute(
                "UPDATE key_slots SET state = 'DESTROYED' WHERE purpose = ? AND version = ?",
                (purpose, old_version),
            )
    return version
