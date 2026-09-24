"""Signer trust states: verification trust apart from import trust (comms v0.3 A11, design §B.4).

| State | Verifies | Imports |
|---|---|---|
| ``ACTIVE`` | yes | yes |
| ``TRUSTED_RETIRED`` (a normal rotation) | yes | yes |
| ``VERIFICATION_ONLY`` (compromise; what it signed stays checkable) | yes | no |
| ``REVOKED`` (compromise; listed for forensics only) | no | no |

Transitions only ever lose trust, enforced here and by a database trigger. Every change is
an audited ``admin.signer_trust`` event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core.audit.writer import AuditWriter
from comms.core.keys.slots import KeySlotError

__all__ = ["TRUST_ORDER", "can_verify", "listed_signers", "mark_signer", "require_import_trust"]

TRUST_ORDER = ("ACTIVE", "TRUSTED_RETIRED", "VERIFICATION_ONLY", "REVOKED")  # most to least
_VERIFIES = frozenset({"ACTIVE", "TRUSTED_RETIRED", "VERIFICATION_ONLY"})
_IMPORTS = frozenset({"ACTIVE", "TRUSTED_RETIRED"})


def _state(conn: Any, key_id: str) -> str | None:
    row = conn.execute(
        "SELECT trust_state FROM verification_keys WHERE key_id = ?", (key_id,)
    ).fetchone()
    return None if row is None else str(row[0])


def can_verify(conn: Any, key_id: str) -> bool:
    return _state(conn, key_id) in _VERIFIES


def require_import_trust(conn: Any, key_id: str) -> None:
    if _state(conn, key_id) not in _IMPORTS:
        raise KeySlotError("this signer is not trusted for import")


def listed_signers(conn: Any, purpose: str) -> list[dict[str, Any]]:
    """Every registered signer of ``purpose``, whatever its state (revoked ones included)."""
    names = ("key_id", "trust_state", "activated_at", "retired_at")
    return [
        dict(zip(names, row, strict=True))
        for row in conn.execute(
            f"SELECT {', '.join(names)} FROM verification_keys WHERE purpose = ? ORDER BY activated_at, key_id",
            (purpose,),
        )
    ]


def mark_signer(writer: AuditWriter, key_id: str, state: str, *, now: datetime) -> None:
    """Move a signer to a state of less trust; refuses anything else."""
    if state not in TRUST_ORDER:
        raise KeySlotError("unknown trust state")
    with writer.transaction() as tx:
        current = _state(tx.conn, key_id)
        if current is None:
            raise KeySlotError("unknown signer")
        if TRUST_ORDER.index(state) <= TRUST_ORDER.index(current):
            raise KeySlotError("signer trust is one-way toward less trust")
        tx.conn.execute(
            "UPDATE verification_keys SET trust_state = ?, retired_at = COALESCE(retired_at, ?) WHERE key_id = ?",
            (state, tx.stamp, key_id),
        )
        tx.append(
            "admin.signer_trust",
            payload={"key_id": key_id, "from_state": current, "to_state": state},
        )
