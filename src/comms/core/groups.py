"""Groups: the opaque ``grp_`` ref of a Telegram group or channel destination (comms v0.3 D1).

A group maps one to one to a destination whose platform identity is a group or a channel; the
database refuses a private chat and any second mapping, and a mapping never changes.
``group_ref`` gets the destination's group ref, minting it on first use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core import refs, timeutil
from comms.core.storage.db import write_tx

__all__ = ["GroupError", "destination_of", "group_ref"]


class GroupError(Exception):
    """A fixed, non-enumerating refusal."""


def group_ref(conn: Any, destination_ref: str, *, now: datetime) -> str:
    try:
        refs.check(destination_ref, "destination")
    except ValueError:
        raise GroupError("unknown destination") from None
    with write_tx(conn):
        row = conn.execute(
            "SELECT d.id, d.platform_identity, g.ref FROM destinations d"
            " LEFT JOIN groups g ON g.destination_id = d.id WHERE d.ref = ?",
            (destination_ref,),
        ).fetchone()
        if row is None:
            raise GroupError("unknown destination")
        destination_id, identity, existing = row
        if existing is not None:
            return str(existing)
        if not identity.startswith(("group:", "channel:")):
            raise GroupError("not a group destination")
        ref = refs.mint("group")
        conn.execute(
            "INSERT INTO groups (ref, destination_id, created_at) VALUES (?, ?, ?)",
            (ref, destination_id, timeutil.iso(now)),
        )
        return ref


def destination_of(conn: Any, grp: str) -> str:
    try:
        refs.check(grp, "group")
    except ValueError:
        raise GroupError("unknown group") from None
    row = conn.execute(
        "SELECT d.ref FROM groups g JOIN destinations d ON d.id = g.destination_id WHERE g.ref = ?",
        (grp,),
    ).fetchone()
    if row is None:
        raise GroupError("unknown group")
    return str(row[0])
