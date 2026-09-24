"""Durable opaque refs for provider objects (comms v0.3 Task D1b; G13).

A provider message, invite, template, topic or media item gets one opaque ref
(``cmg_``/``inv_``/``ctp_``/``top_``/``med_``) per ``(kind, transport, actor, destination,
provider identity)``, stable across restarts, so a later reply, edit or delete names it by ref.
The provider identity lives only inside SQLCipher and in the adapters; it is never in a repr
or an error, and only ``admin.identity.inspect`` returns it (D17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from comms.core import refs, timeutil
from comms.core.errors import CommsError
from comms.core.storage.db import write_tx

__all__ = ["KIND_PREFIX", "ProviderObject", "object_ref", "resolve_object"]

KIND_PREFIX = MappingProxyType(
    {kind: refs.CORE_PREFIXES[kind] for kind in ("message", "invite", "template", "topic", "media")}
)
_TRANSPORTS = frozenset({"telegram", "whatsapp"})


@dataclass(frozen=True)
class ProviderObject:
    ref: str
    kind: str
    transport: str
    actor: str
    destination_id: int | None
    provider_identity: str = field(repr=False)


def object_ref(
    conn: Any,
    kind: str,
    transport: str,
    actor: str,
    destination_id: int | None,
    provider_identity: str,
    *,
    now: datetime,
) -> str:
    """The object's ref: the existing one (its ``last_seen_at`` refreshed) or a new one."""
    if kind not in KIND_PREFIX or transport not in _TRANSPORTS:
        raise CommsError("INVALID_ARGUMENT")
    if (
        not isinstance(actor, str)
        or not actor
        or not isinstance(provider_identity, str)
        or not provider_identity
    ):
        raise CommsError("INVALID_ARGUMENT")
    stamp = timeutil.iso(now)
    key = (kind, transport, actor, destination_id or 0, provider_identity)
    with write_tx(conn):
        row = conn.execute(
            "SELECT id, ref FROM provider_objects WHERE kind = ? AND transport = ? AND actor = ?"
            " AND ifnull(destination_id, 0) = ? AND provider_identity = ?",
            key,
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE provider_objects SET last_seen_at = ? WHERE id = ?", (stamp, row[0])
            )
            return str(row[1])
        ref = refs.mint(kind)
        conn.execute(
            "INSERT INTO provider_objects (ref, kind, transport, actor, destination_id,"
            " provider_identity, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ref, kind, transport, actor, destination_id, provider_identity, stamp, stamp),
        )
        return ref


def resolve_object(conn: Any, ref: str, expected_kind: str) -> ProviderObject:
    if expected_kind not in KIND_PREFIX:
        raise CommsError("INVALID_ARGUMENT")
    kind = next(
        (k for k, prefix in KIND_PREFIX.items() if isinstance(ref, str) and ref.startswith(prefix)),
        None,
    )
    if kind is None:
        raise CommsError("INVALID_ARGUMENT")
    if kind != expected_kind:
        raise CommsError("INVALID_ARGUMENT")
    try:
        refs.check(ref, kind)
    except ValueError:
        raise CommsError("INVALID_ARGUMENT") from None
    row = conn.execute(
        "SELECT kind, transport, actor, destination_id, provider_identity FROM provider_objects"
        " WHERE ref = ?",
        (ref,),
    ).fetchone()
    if row is None:
        raise CommsError("NOT_FOUND")
    return ProviderObject(ref, row[0], row[1], row[2], row[3], row[4])
