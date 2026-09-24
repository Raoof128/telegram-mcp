"""Running scheduled campaigns when they are due (design §8, C6).

The time gate is enforced twice: ``run_due`` only moves a campaign whose generation's
``send_at`` has passed to ``SENDING``, and a job is claimable only while its campaign is
``SENDING`` (the reducer's claim compare-and-set). No re-authorization happens here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.campaigns.events import append_event
from comms.core.delivery.engine import Engine, ExecutorLease, require_lease
from comms.core.storage.db import write_tx

__all__ = ["run_due"]


def run_due(lease: ExecutorLease, conn: Any, engine: Engine, *, now: datetime) -> list[str]:
    """Start and execute every SCHEDULED campaign whose send time is at or before ``now``."""
    require_lease(lease)
    stamp = timeutil.iso(now)
    due = [
        row[0]
        for row in conn.execute(
            "SELECT c.ref FROM campaigns c JOIN generations g ON g.id = c.current_generation_id"
            " WHERE c.lifecycle = 'SCHEDULED' AND g.status = 'active' AND g.send_at <= ?"
            " ORDER BY g.send_at, c.ref",
            (stamp,),
        )
    ]
    started = []
    for cmp in due:
        with write_tx(conn):
            cur = conn.execute(
                "UPDATE campaigns SET lifecycle = 'SENDING', updated_at = ?"
                " WHERE ref = ? AND lifecycle = 'SCHEDULED'",
                (stamp, cmp),
            )
            if cur.rowcount != 1:
                continue
            gen = conn.execute(
                "SELECT g.ref FROM campaigns c JOIN generations g ON g.id = c.current_generation_id"
                " WHERE c.ref = ?",
                (cmp,),
            ).fetchone()[0]
            append_event(
                conn,
                "campaign.send_started",
                cmp,
                {"generation": gen, "lifecycle": "SENDING"},
                now=now,
            )
        started.append(cmp)
        engine.execute(lease, cmp)
    return started
