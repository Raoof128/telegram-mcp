"""Target resolution with origin paths, and path validity (design §3, §5.3).

A target resolves through the audience DAG: an audience to its members, a
location to its destinations and member recipients, a recipient to its contact
points. Every endpoint is recorded with the chain of refs that reached it (its
origin path). Candidates group endpoints by ``(transport, identity_id)`` — the
delivery identity — so one identity yields one candidate, however many
endpoints and paths reach it (R3, S1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from comms.core import refs
from comms.core.campaigns.directory import DirectoryError

__all__ = ["Candidate", "Origin", "Targets", "path_is_valid", "resolve_targets"]

Targets = Mapping[str, Sequence[str]]
_TARGET_KINDS = {
    "audiences": "audience",
    "locations": "location",
    "destinations": "destination",
    "recipients": "recipient",
}


@dataclass(frozen=True)
class Origin:
    endpoint_ref: str
    path: tuple[str, ...]  # path[-1] == endpoint_ref


@dataclass(frozen=True)
class Candidate:
    transport: str
    identity_id: int
    endpoint_refs: tuple[str, ...]
    origins: tuple[Origin, ...]


def _validated_targets(conn: Any, targets: Targets) -> list[tuple[str, str]]:
    if not isinstance(targets, Mapping) or not set(targets) <= set(_TARGET_KINDS):
        raise DirectoryError("unknown target")
    found = []
    for key, kind in _TARGET_KINDS.items():
        values = targets.get(key, ())
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise DirectoryError("unknown target")
        for ref in values:
            try:
                refs.check(ref, kind)
            except ValueError:
                raise DirectoryError("unknown target") from None
            table = {
                "audience": "audiences",
                "location": "locations",
                "destination": "destinations",
                "recipient": "recipients",
            }[kind]
            if conn.execute(f"SELECT 1 FROM {table} WHERE ref = ?", (ref,)).fetchone() is None:
                raise DirectoryError("unknown target")
            found.append((kind, ref))
    return sorted(set(found))


def resolve_targets(conn: Any, targets: Targets, transports: frozenset[str]) -> list[Candidate]:
    """Every sendable endpoint reachable from ``targets`` on ``transports``, grouped by identity."""
    groups: dict[tuple[str, int], tuple[set[str], set[Origin]]] = {}

    def emit(transport: str, identity_id: int, endpoint_ref: str, path: tuple[str, ...]) -> None:
        refs_, origins = groups.setdefault((transport, identity_id), (set(), set()))
        refs_.add(endpoint_ref)
        origins.add(Origin(endpoint_ref, path))

    for kind, ref in _validated_targets(conn, targets):
        stack: list[tuple[str, str, tuple[str, ...]]] = [(kind, ref, (ref,))]
        while stack:
            kind, ref, path = stack.pop()
            if kind == "audience":
                aud_id = conn.execute("SELECT id FROM audiences WHERE ref = ?", (ref,)).fetchone()[
                    0
                ]
                for member_kind, member_ref in _audience_members(conn, aud_id):
                    if member_kind == "audience" and member_ref in path:
                        raise DirectoryError("audience cycle")
                    stack.append((member_kind, member_ref, (*path, member_ref)))
            elif kind == "location":
                loc_id, enabled = conn.execute(
                    "SELECT id, enabled FROM locations WHERE ref = ?", (ref,)
                ).fetchone()
                if not enabled:
                    continue
                for (dst_ref,) in conn.execute(
                    "SELECT ref FROM destinations WHERE location_id = ?", (loc_id,)
                ).fetchall():
                    stack.append(("destination", dst_ref, (*path, dst_ref)))
                for (rcp_ref,) in conn.execute(
                    "SELECT r.ref FROM location_members m JOIN recipients r ON r.id = m.recipient_id"
                    " WHERE m.location_id = ?",
                    (loc_id,),
                ).fetchall():
                    stack.append(("recipient", rcp_ref, (*path, rcp_ref)))
            elif kind == "destination":
                if "telegram" not in transports:
                    continue
                row = conn.execute(
                    "SELECT d.transport, d.identity_id FROM destinations d"
                    " JOIN locations l ON l.id = d.location_id"
                    " WHERE d.ref = ? AND d.enabled = 1 AND l.enabled = 1",
                    (ref,),
                ).fetchone()
                if row is not None:
                    emit(row[0], int(row[1]), ref, path)
            elif kind == "recipient":
                for cp_ref, transport, identity_id in conn.execute(
                    "SELECT c.ref, c.transport, c.identity_id FROM contact_points c"
                    " JOIN recipients r ON r.id = c.recipient_id"
                    " WHERE r.ref = ? AND r.enabled = 1 AND c.enabled = 1 AND c.opted_out_at IS NULL",
                    (ref,),
                ).fetchall():
                    if transport in transports:
                        emit(transport, int(identity_id), cp_ref, (*path, cp_ref))
    return [
        Candidate(
            transport=transport,
            identity_id=identity_id,
            endpoint_refs=tuple(sorted(endpoint_refs)),
            origins=tuple(sorted(origins, key=lambda o: o.path)),
        )
        for (transport, identity_id), (endpoint_refs, origins) in sorted(groups.items())
    ]


def _audience_members(conn: Any, audience_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT 'audience', a.ref FROM audience_members m JOIN audiences a ON a.id = m.member_audience_id"
        " WHERE m.audience_id = ?1"
        " UNION ALL SELECT 'location', l.ref FROM audience_members m"
        " JOIN locations l ON l.id = m.member_location_id WHERE m.audience_id = ?1"
        " UNION ALL SELECT 'destination', d.ref FROM audience_members m"
        " JOIN destinations d ON d.id = m.member_destination_id WHERE m.audience_id = ?1"
        " UNION ALL SELECT 'recipient', r.ref FROM audience_members m"
        " JOIN recipients r ON r.id = m.member_recipient_id WHERE m.audience_id = ?1",
        (audience_id,),
    ).fetchall()
    return sorted((str(k), str(r)) for k, r in rows)


def _node_valid(conn: Any, kind: str, ref: str) -> bool:
    if kind == "audience":
        return conn.execute("SELECT 1 FROM audiences WHERE ref = ?", (ref,)).fetchone() is not None
    if kind == "location":
        sql = "SELECT 1 FROM locations WHERE ref = ? AND enabled = 1"
    elif kind == "destination":
        sql = (
            "SELECT 1 FROM destinations d JOIN locations l ON l.id = d.location_id"
            " WHERE d.ref = ? AND d.enabled = 1 AND l.enabled = 1"
        )
    elif kind == "recipient":
        sql = "SELECT 1 FROM recipients WHERE ref = ? AND enabled = 1"
    elif kind == "contact_point":
        sql = (
            "SELECT 1 FROM contact_points c JOIN recipients r ON r.id = c.recipient_id"
            " WHERE c.ref = ? AND c.enabled = 1 AND c.opted_out_at IS NULL AND r.enabled = 1"
        )
    else:
        return False
    return conn.execute(sql, (ref,)).fetchone() is not None


_EDGES = {
    ("audience", "audience"): "member_audience_id",
    ("audience", "location"): "member_location_id",
    ("audience", "destination"): "member_destination_id",
    ("audience", "recipient"): "member_recipient_id",
}
_TABLES = {
    "audience": "audiences",
    "location": "locations",
    "destination": "destinations",
    "recipient": "recipients",
    "contact_point": "contact_points",
}


def _edge_valid(conn: Any, parent: tuple[str, str], child: tuple[str, str]) -> bool:
    (pk, pref), (ck, cref) = parent, child
    if (pk, ck) in _EDGES:
        column = _EDGES[(pk, ck)]
        sql = (
            f"SELECT 1 FROM audience_members m WHERE m.audience_id = (SELECT id FROM audiences WHERE ref = ?)"
            f" AND m.{column} = (SELECT id FROM {_TABLES[ck]} WHERE ref = ?)"
        )
    elif (pk, ck) == ("location", "destination"):
        sql = (
            "SELECT 1 FROM destinations d JOIN locations l ON l.id = d.location_id"
            " WHERE l.ref = ? AND d.ref = ?"
        )
    elif (pk, ck) == ("location", "recipient"):
        sql = (
            "SELECT 1 FROM location_members m JOIN locations l ON l.id = m.location_id"
            " JOIN recipients r ON r.id = m.recipient_id WHERE l.ref = ? AND r.ref = ?"
        )
    elif (pk, ck) == ("recipient", "contact_point"):
        sql = (
            "SELECT 1 FROM contact_points c JOIN recipients r ON r.id = c.recipient_id"
            " WHERE r.ref = ? AND c.ref = ?"
        )
    else:
        return False
    return conn.execute(sql, (pref, cref)).fetchone() is not None


def path_is_valid(conn: Any, path: tuple[str, ...]) -> bool:
    """Every node enabled now and every edge present now; the last node is an endpoint (§5.3)."""
    if not path:
        return False
    nodes = []
    for ref in path:
        try:
            nodes.append((refs.kind_of(ref), ref))
        except ValueError:
            return False
    if nodes[-1][0] not in ("destination", "contact_point"):
        return False
    if not all(_node_valid(conn, kind, ref) for kind, ref in nodes):
        return False
    return all(_edge_valid(conn, a, b) for a, b in pairwise(nodes))
