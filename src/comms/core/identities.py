"""Provider identities behind a ref, for explicit owner inspection only (comms v0.3 D17; P §44).

This is the one read that returns delivery identities, chat ids or provider object ids. Every
other view carries refs; ``admin.identity.inspect`` is its only caller.
"""

from __future__ import annotations

from typing import Any

from comms.core import refs
from comms.core.campaigns.directory import DirectoryNotFound

__all__ = ["identities_of"]

_OBJECTS = frozenset({"message", "invite", "template", "topic", "media"})


def identities_of(conn: Any, ref: str) -> list[dict[str, str]]:
    """``[{transport, identity}]`` for a group, destination, recipient, contact point or object."""
    try:
        kind = refs.kind_of(ref)
    except ValueError:
        raise DirectoryNotFound("unknown ref") from None
    if kind == "group":
        rows = conn.execute(
            "SELECT i.transport, i.identity FROM groups g JOIN destinations d"
            " ON d.id = g.destination_id JOIN delivery_identities i ON i.id = d.identity_id"
            " WHERE g.ref = ?",
            (ref,),
        ).fetchall()
    elif kind in ("destination", "contact_point"):
        table = "destinations" if kind == "destination" else "contact_points"
        rows = conn.execute(
            f"SELECT i.transport, i.identity FROM {table} e"
            " JOIN delivery_identities i ON i.id = e.identity_id WHERE e.ref = ?",
            (ref,),
        ).fetchall()
    elif kind == "recipient":
        if conn.execute("SELECT 1 FROM recipients WHERE ref = ?", (ref,)).fetchone() is None:
            raise DirectoryNotFound("unknown ref")
        rows = conn.execute(
            "SELECT i.transport, i.identity FROM contact_points c JOIN recipients r"
            " ON r.id = c.recipient_id JOIN delivery_identities i ON i.id = c.identity_id"
            " WHERE r.ref = ? ORDER BY c.id",
            (ref,),
        ).fetchall()
        return [{"transport": t, "identity": i} for t, i in rows]
    elif kind in _OBJECTS:
        rows = conn.execute(
            "SELECT transport, provider_identity FROM provider_objects WHERE ref = ?", (ref,)
        ).fetchall()
    else:
        raise DirectoryNotFound("unknown ref")
    if not rows:
        raise DirectoryNotFound("unknown ref")
    return [{"transport": t, "identity": i} for t, i in rows]
