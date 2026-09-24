"""Campaign-body redaction (comms v0.3 A15, Task B19): bodies go, every digest stays.

For a campaign that is ``COMPLETE`` or ``CANCELLED``, each generation frozen before the
cutoff loses its bodies: every job payload that no unresolved delivery can still need
becomes NULL, then the generation's content becomes ``'{}'``, then (once all its
generations are redacted) the campaign's own draft content does too. ``redacted_at`` is set
exactly once; the database refuses any other edit of a frozen row (schema v3). Snapshot and
payload digests, and the keyed campaign commitment, are untouched, so commitments still
verify. Redaction runs in the caller's transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core import timeutil

__all__ = ["REDACTABLE_LIFECYCLES", "UNRESOLVED_STATES", "redact_campaign_bodies"]

REDACTABLE_LIFECYCLES = ("COMPLETE", "CANCELLED")
# A job in one of these states may still be delivered, retried or resolved.
UNRESOLVED_STATES = ("PENDING", "IN_FLIGHT", "OUTCOME_UNKNOWN", "FAILED_TRANSIENT")


def redact_campaign_bodies(conn: Any, *, cutoff: datetime, now: datetime) -> int:
    """Redact every eligible generation; return how many generations were redacted."""
    if not conn.in_transaction:
        raise RuntimeError("redaction runs inside the caller's transaction")
    stamp, limit = timeutil.iso(now), timeutil.iso(cutoff)
    unresolved = ", ".join(f"'{s}'" for s in UNRESOLVED_STATES)
    candidates = conn.execute(
        "SELECT g.id, c.id FROM generations g JOIN campaigns c ON c.id = g.campaign_id"
        f" WHERE c.lifecycle IN {REDACTABLE_LIFECYCLES!r} AND g.created_at < ? AND g.redacted_at IS NULL",
        (limit,),
    ).fetchall()
    redacted = 0
    for generation_id, campaign_id in candidates:
        conn.execute(
            "UPDATE delivery_jobs SET payload = NULL, redacted_at = ?"
            f" WHERE generation_id = ? AND payload IS NOT NULL AND state NOT IN ({unresolved})",
            (stamp, generation_id),
        )
        still_bodied = conn.execute(
            "SELECT count(*) FROM delivery_jobs WHERE generation_id = ? AND payload IS NOT NULL",
            (generation_id,),
        ).fetchone()[0]
        if still_bodied:
            continue
        conn.execute(
            "UPDATE generations SET content = '{}', redacted_at = ? WHERE id = ?",
            (stamp, generation_id),
        )
        redacted += 1
        remaining = conn.execute(
            "SELECT count(*) FROM generations WHERE campaign_id = ? AND redacted_at IS NULL",
            (campaign_id,),
        ).fetchone()[0]
        if not remaining:
            conn.execute("UPDATE campaigns SET content = '{}' WHERE id = ?", (campaign_id,))
    return redacted
