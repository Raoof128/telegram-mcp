"""Outbound campaign history for one delivery identity (comms v0.3 Task D11; P §20, §21).

A read over the campaign store: the jobs sent to one ``(transport, identity)``, newest first,
by job id. Each row names its campaign, job, state and send time, never the identity or the
payload, and is labelled ``campaign_store`` by the context engine.
"""

from __future__ import annotations

from typing import Any

__all__ = ["identity_history"]


def identity_history(
    conn: Any, transport: str, identity: str, *, limit: int, before: int | None = None
) -> tuple[list[dict[str, Any]], int | None]:
    """Up to ``limit`` jobs older than job id ``before``, and the cursor for the next page."""
    rows = conn.execute(
        "SELECT j.id, j.ref, c.ref, j.state, g.send_at FROM delivery_jobs j"
        " JOIN delivery_identities i ON i.id = j.identity_id"
        " JOIN generations g ON g.id = j.generation_id"
        " JOIN campaigns c ON c.id = g.campaign_id"
        " WHERE i.transport = ? AND i.identity = ? AND j.id < ?"
        " ORDER BY j.id DESC LIMIT ?",
        (transport, identity, before if before is not None else 2**62, limit + 1),
    ).fetchall()
    items = [
        {"job_ref": job, "campaign_ref": cmp, "state": state, "sent_at": send_at}
        for _id, job, cmp, state, send_at in rows[:limit]
    ]
    return items, (int(rows[limit - 1][0]) if len(rows) > limit else None)
