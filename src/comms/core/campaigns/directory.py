"""Locations, destinations, recipients, contact points and audiences (design §3).

A delivery identity is the transport's canonical name for what a message is
actually sent to, computed by the transport's pure ``normalize`` (injected: core
never imports a transport). Its row is shared by every endpoint that normalizes
to it (S1); at most one enabled destination and one enabled contact point hold
it. Identities never change (R11): to move one, disable the old endpoint and
create a new one. Errors carry fixed messages and never an identity.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from comms.core import refs, timeutil
from comms.core.groups import group_ref_in_tx, is_group_identity
from comms.core.storage.db import require_tx, write_tx

__all__ = [
    "DirectoryError",
    "DirectoryNotFound",
    "Normalizer",
    "add_audience",
    "add_audience_in_tx",
    "add_audience_member",
    "add_audience_member_in_tx",
    "add_contact_point",
    "add_contact_point_in_tx",
    "add_destination",
    "add_destination_in_tx",
    "add_location",
    "add_location_in_tx",
    "add_location_member",
    "add_location_member_in_tx",
    "add_recipient",
    "add_recipient_in_tx",
    "destination_id",
    "has_recipient",
    "member_identity",
    "opt_out",
    "opt_out_in_tx",
    "remove_audience_member",
    "remove_audience_member_in_tx",
    "remove_location_member",
    "remove_location_member_in_tx",
    "rename",
    "rename_in_tx",
    "set_enabled",
    "set_enabled_in_tx",
]

Normalizer = Callable[[str], str]

DESTINATION_TRANSPORTS = frozenset({"telegram"})
CONTACT_TRANSPORTS = frozenset({"telegram", "whatsapp"})
_TABLE = {
    "location": "locations",
    "destination": "destinations",
    "recipient": "recipients",
    "contact_point": "contact_points",
    "audience": "audiences",
}
_MEMBER_COLUMN = {
    "location": "member_location_id",
    "audience": "member_audience_id",
    "destination": "member_destination_id",
    "recipient": "member_recipient_id",
}


class DirectoryError(ValueError):
    """A directory write was refused. Messages are fixed and never enumerate data."""


class DirectoryNotFound(DirectoryError):
    """The ref, target or membership names nothing (D16: NOT_FOUND, never INVALID_ARGUMENT)."""


def _kind(ref: object, allowed: frozenset[str] | set[str]) -> str:
    try:
        kind = refs.kind_of(ref)
    except ValueError:
        raise DirectoryNotFound("unknown ref") from None
    if kind not in allowed:
        raise DirectoryNotFound("unknown ref")
    return kind


def _id(conn: Any, kind: str, ref: str) -> int:
    row = conn.execute(f"SELECT id FROM {_TABLE[kind]} WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise DirectoryNotFound("unknown ref")
    return int(row[0])


def _identity_id(conn: Any, transport: str, platform_identity: str, normalize: Normalizer) -> int:
    try:
        value = normalize(platform_identity)
    except Exception:  # noqa: BLE001 -- a normalizer's message may echo the identity
        raise DirectoryError("invalid platform identity") from None
    if not isinstance(value, str) or not value:
        raise DirectoryError("invalid platform identity")
    row = conn.execute(
        "SELECT id FROM delivery_identities WHERE transport = ? AND identity = ?",
        (transport, value),
    ).fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO delivery_identities (transport, identity) VALUES (?, ?)", (transport, value)
    )
    return int(cur.lastrowid)


def _refuse_second_enabled(conn: Any, table: str, identity_id: int) -> None:
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE identity_id = ? AND enabled = 1",
        (identity_id,),
    ).fetchone()
    if row is not None:
        raise DirectoryError("delivery identity already has an enabled endpoint")


def add_location_in_tx(conn: Any, name: str, *, now: datetime) -> str:
    require_tx(conn)
    ref, stamp = refs.mint("location"), timeutil.iso(now)
    conn.execute(
        "INSERT INTO locations (ref, name, created_at) VALUES (?, ?, ?)",
        (ref, str(name), stamp),
    )
    return ref


def add_location(conn: Any, name: str, *, now: datetime) -> str:
    with write_tx(conn):
        return add_location_in_tx(conn, name, now=now)


def add_destination_in_tx(
    conn: Any,
    location_ref: str,
    transport: str,
    platform_identity: str,
    display_name: str,
    *,
    normalize: Normalizer,
    now: datetime,
) -> str:
    require_tx(conn)
    if transport not in DESTINATION_TRANSPORTS:
        raise DirectoryError("unsupported transport")
    _kind(location_ref, {"location"})
    ref, stamp = refs.mint("destination"), timeutil.iso(now)
    location_id = _id(conn, "location", location_ref)
    identity_id = _identity_id(conn, transport, platform_identity, normalize)
    _refuse_second_enabled(conn, "destinations", identity_id)
    conn.execute(
        "INSERT INTO destinations (ref, location_id, transport, platform_identity, identity_id,"
        " display_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ref, location_id, transport, platform_identity, identity_id, str(display_name), stamp),
    )
    if is_group_identity(platform_identity):  # listed from the moment it exists (E11b)
        group_ref_in_tx(conn, ref, now=now)
    return ref


def add_destination(
    conn: Any,
    location_ref: str,
    transport: str,
    platform_identity: str,
    display_name: str,
    *,
    normalize: Normalizer,
    now: datetime,
) -> str:
    with write_tx(conn):
        return add_destination_in_tx(
            conn,
            location_ref,
            transport,
            platform_identity,
            display_name,
            normalize=normalize,
            now=now,
        )


def add_recipient_in_tx(conn: Any, *, now: datetime, display_name: str | None = None) -> str:
    require_tx(conn)
    if display_name is not None and (
        not isinstance(display_name, str) or not 0 < len(display_name.strip()) <= 200
    ):
        raise DirectoryError("display name refused")
    ref, stamp = refs.mint("recipient"), timeutil.iso(now)
    conn.execute(
        "INSERT INTO recipients (ref, display_name, created_at) VALUES (?, ?, ?)",
        (ref, display_name.strip() if display_name else None, stamp),
    )
    return ref


def add_recipient(conn: Any, *, now: datetime, display_name: str | None = None) -> str:
    with write_tx(conn):
        return add_recipient_in_tx(conn, now=now, display_name=display_name)


def named_recipients(conn: Any) -> list[tuple[str, str]]:
    """Enabled recipients with a display name: ``(ref, name)`` (D7 resolution)."""
    rows = conn.execute(
        "SELECT ref, display_name FROM recipients WHERE enabled = 1 AND display_name IS NOT NULL ORDER BY id"
    )
    return [(str(r[0]), str(r[1])) for r in rows]


def group_destinations(conn: Any) -> list[tuple[str, str, str]]:
    """Enabled group and channel destinations: ``(ref, display name, location name)`` (D7)."""
    rows = conn.execute(
        "SELECT d.ref, d.display_name, l.name FROM destinations d JOIN locations l ON l.id = d.location_id"
        " WHERE d.enabled = 1 AND (d.platform_identity GLOB 'group:*' OR d.platform_identity GLOB 'channel:*')"
        " ORDER BY d.id"
    )
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def add_contact_point_in_tx(
    conn: Any,
    recipient_ref: str,
    transport: str,
    platform_identity: str,
    *,
    normalize: Normalizer,
    now: datetime,
) -> str:
    require_tx(conn)
    if transport not in CONTACT_TRANSPORTS:
        raise DirectoryError("unsupported transport")
    _kind(recipient_ref, {"recipient"})
    ref, stamp = refs.mint("contact_point"), timeutil.iso(now)
    recipient_id = _id(conn, "recipient", recipient_ref)
    taken = conn.execute(
        "SELECT 1 FROM contact_points WHERE recipient_id = ? AND transport = ? AND enabled = 1",
        (recipient_id, transport),
    ).fetchone()
    if taken is not None:
        raise DirectoryError("recipient already has an enabled contact point on this transport")
    identity_id = _identity_id(conn, transport, platform_identity, normalize)
    _refuse_second_enabled(conn, "contact_points", identity_id)
    conn.execute(
        "INSERT INTO contact_points (ref, recipient_id, transport, platform_identity,"
        " identity_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ref, recipient_id, transport, platform_identity, identity_id, stamp),
    )
    return ref


def add_contact_point(
    conn: Any,
    recipient_ref: str,
    transport: str,
    platform_identity: str,
    *,
    normalize: Normalizer,
    now: datetime,
) -> str:
    with write_tx(conn):
        return add_contact_point_in_tx(
            conn, recipient_ref, transport, platform_identity, normalize=normalize, now=now
        )


def set_enabled_in_tx(conn: Any, ref: str, enabled: bool, *, now: datetime | None = None) -> None:
    """Enable or disable a directory row; disabling an endpoint records when (B20)."""
    require_tx(conn)
    kind = _kind(ref, {"location", "destination", "recipient", "contact_point"})
    table = _TABLE[kind]
    row_id = _id(conn, kind, ref)
    if enabled and kind in ("destination", "contact_point"):
        identity_id = conn.execute(
            f"SELECT identity_id FROM {table} WHERE id = ?",
            (row_id,),
        ).fetchone()[0]
        clash = conn.execute(
            f"SELECT 1 FROM {table} WHERE identity_id = ? AND enabled = 1 AND id <> ?",
            (identity_id, row_id),
        ).fetchone()
        if clash is not None:
            raise DirectoryError("delivery identity already has an enabled endpoint")
        if kind == "contact_point":
            recipient_id, transport = conn.execute(
                "SELECT recipient_id, transport FROM contact_points WHERE id = ?", (row_id,)
            ).fetchone()
            other = conn.execute(
                "SELECT 1 FROM contact_points WHERE recipient_id = ? AND transport = ?"
                " AND enabled = 1 AND id <> ?",
                (recipient_id, transport, row_id),
            ).fetchone()
            if other is not None:
                raise DirectoryError(
                    "recipient already has an enabled contact point on this transport"
                )
    conn.execute(
        f"UPDATE {table} SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, row_id),
    )
    if kind in ("destination", "contact_point"):
        stamp = None if enabled else timeutil.iso(now or datetime.now(UTC))
        conn.execute(f"UPDATE {table} SET disabled_at = ? WHERE id = ?", (stamp, row_id))


def set_enabled(conn: Any, ref: str, enabled: bool, *, now: datetime | None = None) -> None:
    """Enable or disable a directory row; disabling an endpoint records when (B20)."""
    with write_tx(conn):
        set_enabled_in_tx(conn, ref, enabled, now=now)


def opt_out_in_tx(conn: Any, contact_point_ref: str, *, now: datetime) -> None:
    require_tx(conn)
    _kind(contact_point_ref, {"contact_point"})
    stamp = timeutil.iso(now)
    row_id = _id(conn, "contact_point", contact_point_ref)
    conn.execute(
        "UPDATE contact_points SET opted_out_at = coalesce(opted_out_at, ?) WHERE id = ?",
        (stamp, row_id),
    )


def opt_out(conn: Any, contact_point_ref: str, *, now: datetime) -> None:
    with write_tx(conn):
        opt_out_in_tx(conn, contact_point_ref, now=now)


def add_location_member_in_tx(conn: Any, location_ref: str, recipient_ref: str) -> None:
    require_tx(conn)
    _kind(location_ref, {"location"})
    _kind(recipient_ref, {"recipient"})
    location_id = _id(conn, "location", location_ref)
    recipient_id = _id(conn, "recipient", recipient_ref)
    conn.execute(
        "INSERT OR IGNORE INTO location_members (location_id, recipient_id) VALUES (?, ?)",
        (location_id, recipient_id),
    )


def add_location_member(conn: Any, location_ref: str, recipient_ref: str) -> None:
    with write_tx(conn):
        add_location_member_in_tx(conn, location_ref, recipient_ref)


def remove_location_member_in_tx(conn: Any, location_ref: str, recipient_ref: str) -> None:
    require_tx(conn)
    _kind(location_ref, {"location"})
    _kind(recipient_ref, {"recipient"})
    cur = conn.execute(
        "DELETE FROM location_members WHERE location_id = ? AND recipient_id = ?",
        (_id(conn, "location", location_ref), _id(conn, "recipient", recipient_ref)),
    )
    if cur.rowcount != 1:
        raise DirectoryNotFound("unknown membership")


def remove_location_member(conn: Any, location_ref: str, recipient_ref: str) -> None:
    with write_tx(conn):
        remove_location_member_in_tx(conn, location_ref, recipient_ref)


def add_audience_in_tx(conn: Any, name: str, *, now: datetime) -> str:
    require_tx(conn)
    ref, stamp = refs.mint("audience"), timeutil.iso(now)
    conn.execute(
        "INSERT INTO audiences (ref, name, created_at) VALUES (?, ?, ?)",
        (ref, str(name), stamp),
    )
    return ref


def add_audience(conn: Any, name: str, *, now: datetime) -> str:
    with write_tx(conn):
        return add_audience_in_tx(conn, name, now=now)


def _reaches(conn: Any, start: int, target: int) -> bool:
    """Iterative DFS over member-audience edges from ``start``: is ``target`` reachable?"""
    seen: set[int] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(
            int(row[0])
            for row in conn.execute(
                "SELECT member_audience_id FROM audience_members"
                " WHERE audience_id = ? AND member_audience_id IS NOT NULL",
                (node,),
            )
        )
    return False


def add_audience_member_in_tx(conn: Any, audience_ref: str, member_ref: str) -> None:
    require_tx(conn)
    _kind(audience_ref, {"audience"})
    kind = _kind(member_ref, set(_MEMBER_COLUMN))
    audience_id = _id(conn, "audience", audience_ref)
    member_id = _id(conn, kind, member_ref)
    if kind == "audience" and _reaches(conn, member_id, audience_id):
        raise DirectoryError("audience cycle")
    column = _MEMBER_COLUMN[kind]
    exists = conn.execute(
        f"SELECT 1 FROM audience_members WHERE audience_id = ? AND {column} = ?",
        (audience_id, member_id),
    ).fetchone()
    if exists is None:
        conn.execute(
            f"INSERT INTO audience_members (audience_id, {column}) VALUES (?, ?)",
            (audience_id, member_id),
        )


def add_audience_member(conn: Any, audience_ref: str, member_ref: str) -> None:
    with write_tx(conn):
        add_audience_member_in_tx(conn, audience_ref, member_ref)


def remove_audience_member_in_tx(conn: Any, audience_ref: str, member_ref: str) -> None:
    require_tx(conn)
    _kind(audience_ref, {"audience"})
    kind = _kind(member_ref, set(_MEMBER_COLUMN))
    column = _MEMBER_COLUMN[kind]
    cur = conn.execute(
        f"DELETE FROM audience_members WHERE audience_id = ? AND {column} = ?",
        (_id(conn, "audience", audience_ref), _id(conn, kind, member_ref)),
    )
    if cur.rowcount != 1:
        raise DirectoryNotFound("unknown membership")


def remove_audience_member(conn: Any, audience_ref: str, member_ref: str) -> None:
    with write_tx(conn):
        remove_audience_member_in_tx(conn, audience_ref, member_ref)


def rename_in_tx(conn: Any, ref: str, name: str) -> None:
    """A location's or an audience's new name (P §31 update); its ref never changes."""
    require_tx(conn)
    kind = _kind(ref, {"location", "audience"})
    if not isinstance(name, str) or not name.strip():
        raise DirectoryError("a name is required")
    conn.execute(f"UPDATE {_TABLE[kind]} SET name = ? WHERE id = ?", (name, _id(conn, kind, ref)))


def rename(conn: Any, ref: str, name: str) -> None:
    """A location's or an audience's new name (P §31 update); its ref never changes."""
    with write_tx(conn):
        rename_in_tx(conn, ref, name)


def destination_id(conn: Any, destination_ref: str) -> int | None:
    """The row id of a destination ref (read-only; for provider-object refs, D8)."""
    row = conn.execute("SELECT id FROM destinations WHERE ref = ?", (destination_ref,)).fetchone()
    return int(row[0]) if row else None


def member_identity(conn: Any, recipient_ref: str, transport: str) -> str | None:
    """The recipient's enabled identity on ``transport`` (read-only; group admin, D12)."""
    row = conn.execute(
        "SELECT i.identity FROM contact_points c JOIN recipients r ON r.id = c.recipient_id"
        " JOIN delivery_identities i ON i.id = c.identity_id"
        " WHERE r.ref = ? AND c.transport = ? AND c.enabled = 1",
        (recipient_ref, transport),
    ).fetchone()
    return None if row is None else str(row[0])


def has_recipient(conn: Any, recipient_ref: str) -> bool:
    """Whether the recipient ref names a recipient (read-only)."""
    row = conn.execute("SELECT 1 FROM recipients WHERE ref = ?", (recipient_ref,)).fetchone()
    return row is not None
