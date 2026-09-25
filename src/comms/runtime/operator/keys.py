"""``keys list|rotate|mark-signer`` (D39-PRE E7; spec A9–A11).

``keys provision`` and the ``comms-db-key`` rotation are local, not here: provisioning runs
before the daemon exists (R-E4), and the rekey closes and reopens the database connection that
every service of a running daemon holds, so it runs with the daemon stopped.
"""

from __future__ import annotations

import os
from typing import Any

from comms.core.keys import rotate as rot
from comms.core.keys.signers import TRUST_ORDER, mark_signer
from comms.core.keys.slots import KeySlotError, active_version
from comms.runtime.operator.context import OperatorContext, OperatorHandler

__all__ = ["KEY_HANDLERS", "LOCAL_ONLY_PURPOSES"]

LOCAL_ONLY_PURPOSES = frozenset({"comms-db-key"})


def _list(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT purpose, version, key_id, state FROM key_slots ORDER BY purpose, version"
    ).fetchall()
    return {
        "keys": [{"purpose": p, "version": int(v), "key_id": k, "state": s} for p, v, k, s in rows]
    }


def _rotate(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    purpose = args.get("purpose")
    if purpose in LOCAL_ONLY_PURPOSES:
        raise ValueError("the database key rotates with the daemon stopped")
    try:
        version = rot.rotate(
            ctx.writer,
            ctx.store,
            str(purpose),
            material=os.urandom(32),
            prove=lambda material: None,  # local key material: nothing remote to prove (A13)
            now=ctx.clock(),
        )
    except KeySlotError as refused:
        raise ValueError(str(refused)) from None
    active = active_version(ctx.conn, str(purpose))
    return {"purpose": purpose, "version": version, "key_id": None if active is None else active[1]}


def _mark_signer(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    key_id, state = args.get("key_id"), args.get("state")
    if not isinstance(key_id, str) or state not in TRUST_ORDER:
        raise ValueError("mark-signer needs --key-id and a --state of less trust")
    try:
        mark_signer(ctx.writer, key_id, state, now=ctx.clock())
    except KeySlotError as refused:
        raise ValueError(str(refused)) from None
    return {"key_id": key_id, "state": state}


KEY_HANDLERS: dict[tuple[str, ...], OperatorHandler] = {
    ("keys", "list"): _list,
    ("keys", "rotate"): _rotate,
    ("keys", "mark-signer"): _mark_signer,
}
