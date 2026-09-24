"""Startup recovery (design §10, R7, R20, S9).

Repairs durable state and never performs an external effect: every ``IN_FLIGHT`` job
becomes ``OUTCOME_UNKNOWN`` with its open attempt closed, every ``SENDING`` campaign with
no pending or in-flight work is completed with a re-derived summary, and the campaigns
that still have ``PENDING`` work are returned for the caller to execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.core.delivery.engine import ExecutorLease, require_lease
from comms.core.delivery.reducer import Evidence, complete_if_idle, reduce, refresh_summary
from comms.core.storage.db import write_tx

__all__ = ["RecoveryReport", "recover"]


@dataclass(frozen=True)
class RecoveryReport:
    marked_unknown: int
    completed: tuple[str, ...]
    resumable: tuple[str, ...]


def recover(lease: ExecutorLease, conn: Any, *, now: datetime) -> RecoveryReport:
    require_lease(lease)
    campaigns = [
        tuple(row)
        for row in conn.execute(
            "SELECT DISTINCT c.id, c.ref FROM campaigns c WHERE c.lifecycle = 'SENDING'"
            " OR EXISTS (SELECT 1 FROM generations g JOIN delivery_jobs j ON j.generation_id = g.id"
            " WHERE g.campaign_id = c.id AND j.state = 'IN_FLIGHT') ORDER BY c.ref"
        )
    ]
    marked = 0
    completed: list[str] = []
    resumable: list[str] = []
    for campaign_id, cmp in campaigns:
        with write_tx(conn):
            before = _lifecycle(conn, campaign_id)
            in_flight = [
                row[0]
                for row in conn.execute(
                    "SELECT j.id FROM delivery_jobs j JOIN generations g ON g.id = j.generation_id"
                    " WHERE g.campaign_id = ? AND j.state = 'IN_FLIGHT' ORDER BY j.id",
                    (campaign_id,),
                )
            ]
            for job_id in in_flight:
                t = reduce(conn, job_id, None, Evidence.RECOVER, None, now=now)
                marked += t.disposition == "applied"
            if _lifecycle(conn, campaign_id) == "SENDING":
                refresh_summary(conn, campaign_id, now=now)
                complete_if_idle(conn, campaign_id, now=now)
            after = _lifecycle(conn, campaign_id)
        if after == "SENDING":
            resumable.append(cmp)
        elif before == "SENDING" and after == "COMPLETE":
            completed.append(cmp)
    return RecoveryReport(
        marked_unknown=marked, completed=tuple(completed), resumable=tuple(resumable)
    )


def _lifecycle(conn: Any, campaign_id: int) -> str:
    return str(
        conn.execute("SELECT lifecycle FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()[0]
    )
