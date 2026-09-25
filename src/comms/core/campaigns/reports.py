"""Campaign reads for the service layer (comms v0.3 Task D15; P §30).

Views carry refs, states, counts and times — never a delivery identity, a payload or a body.
Pages run newest first by row id; a cursor is the last row id seen.
"""

from __future__ import annotations

from typing import Any

from comms.core.campaigns.drafts import NotFound, load

__all__ = ["campaign_view", "job_counts", "job_page", "list_campaigns"]


def campaign_view(conn: Any, cmp: str) -> dict[str, Any]:
    campaign = load(conn, cmp)
    row = conn.execute(
        "SELECT c.title, c.created_at, c.updated_at, g.ref, g.send_at FROM campaigns c"
        " LEFT JOIN generations g ON g.id = c.current_generation_id WHERE c.id = ?",
        (campaign["id"],),
    ).fetchone()
    return {
        "campaign": cmp,
        "title": row[0],
        "lifecycle": campaign["lifecycle"],
        "summary": campaign["summary"],
        "generation": row[3],
        "send_at": row[4],
        "created_at": row[1],
        "updated_at": row[2],
    }


def list_campaigns(
    conn: Any, *, limit: int, before: int | None = None
) -> tuple[list[dict[str, Any]], int | None]:
    rows = conn.execute(
        "SELECT id, ref, title, lifecycle, summary, updated_at FROM campaigns WHERE id < ?"
        " ORDER BY id DESC LIMIT ?",
        (before if before is not None else 2**62, limit + 1),
    ).fetchall()
    items = [
        {"campaign": r[1], "title": r[2], "lifecycle": r[3], "summary": r[4], "updated_at": r[5]}
        for r in rows[:limit]
    ]
    return items, (int(rows[limit - 1][0]) if len(rows) > limit else None)


def job_counts(conn: Any, cmp: str) -> dict[str, int]:
    """Jobs of the current generation, counted by state."""
    campaign = load(conn, cmp)
    rows = conn.execute(
        "SELECT state, count(*) FROM delivery_jobs WHERE generation_id = ? GROUP BY state",
        (campaign["generation_id"],),
    ).fetchall()
    return {str(state): int(n) for state, n in rows}


def job_page(
    conn: Any, cmp: str, *, limit: int, before: int | None = None
) -> tuple[list[dict[str, Any]], int | None]:
    """The current generation's jobs: ref, transport, state and attempts, newest first."""
    campaign = load(conn, cmp)
    if campaign["generation_id"] is None:
        raise NotFound("campaign has no generation")
    rows = conn.execute(
        "SELECT id, ref, transport, state, attempt_count FROM delivery_jobs"
        " WHERE generation_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
        (campaign["generation_id"], before if before is not None else 2**62, limit + 1),
    ).fetchall()
    items = [
        {"job": r[1], "transport": r[2], "state": r[3], "attempts": r[4]} for r in rows[:limit]
    ]
    return items, (int(rows[limit - 1][0]) if len(rows) > limit else None)
