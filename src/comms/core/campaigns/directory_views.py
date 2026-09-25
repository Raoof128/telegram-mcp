"""Location and audience reads for the service layer (comms v0.3 Task D16; P §31).

Views carry refs, names, flags and counts — never a delivery identity. Lists page newest first
by row id; a cursor is the last row id seen.
"""

from __future__ import annotations

from typing import Any

from comms.core import refs
from comms.core.campaigns.directory import DirectoryNotFound

__all__ = ["audience_view", "list_audiences", "list_locations", "location_view"]

_MEMBER_KINDS = ("location", "audience", "destination", "recipient")


def _row_id(conn: Any, table: str, kind: str, ref: object) -> int:
    try:
        refs.check(ref, kind)
    except ValueError:
        raise DirectoryNotFound("unknown ref") from None
    row = conn.execute(f"SELECT id FROM {table} WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise DirectoryNotFound("unknown ref")
    return int(row[0])


def location_view(conn: Any, ref: str) -> dict[str, Any]:
    row_id = _row_id(conn, "locations", "location", ref)
    name, enabled, created = conn.execute(
        "SELECT name, enabled, created_at FROM locations WHERE id = ?", (row_id,)
    ).fetchone()
    members = conn.execute(
        "SELECT count(*) FROM location_members WHERE location_id = ?", (row_id,)
    ).fetchone()[0]
    destinations = conn.execute(
        "SELECT count(*) FROM destinations WHERE location_id = ? AND enabled = 1", (row_id,)
    ).fetchone()[0]
    return {
        "location": ref,
        "name": name,
        "enabled": bool(enabled),
        "members": int(members),
        "destinations": int(destinations),
        "created_at": created,
    }


def audience_view(conn: Any, ref: str) -> dict[str, Any]:
    row_id = _row_id(conn, "audiences", "audience", ref)
    name, created = conn.execute(
        "SELECT name, created_at FROM audiences WHERE id = ?", (row_id,)
    ).fetchone()
    counts = {
        kind: int(
            conn.execute(
                f"SELECT count(*) FROM audience_members WHERE audience_id = ?"
                f" AND member_{kind}_id IS NOT NULL",
                (row_id,),
            ).fetchone()[0]
        )
        for kind in _MEMBER_KINDS
    }
    return {"audience": ref, "name": name, "members": counts, "created_at": created}


def _page(
    conn: Any, sql: str, keys: tuple[str, ...], *, limit: int, before: int | None
) -> tuple[list[dict[str, Any]], int | None]:
    rows = conn.execute(sql, (before if before is not None else 2**62, limit + 1)).fetchall()
    items = [dict(zip(keys, r[1:], strict=True)) for r in rows[:limit]]
    return items, (int(rows[limit - 1][0]) if len(rows) > limit else None)


def list_locations(
    conn: Any, *, limit: int, before: int | None = None
) -> tuple[list[dict[str, Any]], int | None]:
    return _page(
        conn,
        "SELECT id, ref, name, enabled FROM locations WHERE id < ? ORDER BY id DESC LIMIT ?",
        ("location", "name", "enabled"),
        limit=limit,
        before=before,
    )


def list_audiences(
    conn: Any, *, limit: int, before: int | None = None
) -> tuple[list[dict[str, Any]], int | None]:
    return _page(
        conn,
        "SELECT id, ref, name FROM audiences WHERE id < ? ORDER BY id DESC LIMIT ?",
        ("audience", "name"),
        limit=limit,
        before=before,
    )
