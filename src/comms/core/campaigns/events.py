"""The campaign event log: an append-only operational journal inside the encrypted
comms.db. It is not the tamper-evident audit chain (design §9, H4); 5c integrates it.

Every event is appended in the transaction of the state change it records (R15).
Payload privacy is enforced by type, not by string shape (S6): each key has a fixed
validator over a finite domain, and nothing else is accepted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from comms.core import domains, refs, timeutil
from comms.core.canonical import jcs_dumps
from comms.core.validators import Validator, count, digest, one_of, ref, time

__all__ = [
    "EVENT_FIELDS",
    "EVENT_TYPES",
    "LIFECYCLES",
    "SUMMARIES",
    "ReasonCode",
    "append_event",
    "journal_digest",
]

EVENT_TYPES = frozenset(
    {
        "campaign.created",
        "campaign.modified",
        "campaign.validated",
        "campaign.scheduled",
        "campaign.unscheduled",
        "campaign.send_started",
        "campaign.transport_completed",
        "campaign.retry_started",
        "campaign.completed",
        "campaign.summary_changed",
        "campaign.cancelled",
        "delivery.outcome_resolved",
        "delivery.provider_update_refused",
    }
)
LIFECYCLES = frozenset({"DRAFT", "READY", "SCHEDULED", "SENDING", "COMPLETE", "CANCELLED"})
SUMMARIES = frozenset({"IN_PROGRESS", "INDETERMINATE", "SENT", "PARTIAL", "CANCELLED", "FAILED"})


class ReasonCode(StrEnum):
    """The closed set of reasons an event may carry (G8)."""

    OPERATOR = "OPERATOR"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    NOT_CURRENT_ATTEMPT = "NOT_CURRENT_ATTEMPT"
    REDUCER_REFUSED = "REDUCER_REFUSED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"


_COUNTS = (
    "job_count",
    "pending_count",
    "skipped_count",
    "cancelled_count",
    "success_count",
    "failure_count",
    "unknown_count",
    "in_flight_count",
    "already_sent_count",
    "requeued_count",
    "exhausted_count",
)
EVENT_FIELDS: Mapping[str, Validator] = {
    "generation": ref("generation"),
    "job": ref("job"),
    "attempt": ref("attempt"),
    "transport": one_of({"telegram", "whatsapp"}),
    "target_digest": digest,
    "recipient_digest": digest,
    "snapshot_digest": digest,
    **{name: count(0) for name in _COUNTS},
    "attempt_no": count(1),
    "summary": one_of(SUMMARIES),
    "lifecycle": one_of(LIFECYCLES),
    "reason": one_of({r.value for r in ReasonCode}),
    "send_at": time,
    "verdict": one_of({"sent", "not_sent"}),
    "status": one_of({"ACCEPTED", "DELIVERED", "FAILED_PERMANENT"}),
}


def journal_digest(row: Mapping[str, Any]) -> str:
    """The digest of a complete journal row (comms v0.3 G4): every committed field but itself."""
    body = {k: row[k] for k in ("campaign_ref", "event_ref", "event_type", "payload", "ts")}
    return hashlib.sha256(domains.CAMPAIGN_EVENT + jcs_dumps(body)).hexdigest()


def append_event(
    conn: Any,
    event_type: str,
    campaign_ref: str | None,
    payload: Mapping[str, Any],
    *,
    now: datetime,
    audit: Any = None,
) -> str:
    """Append one event inside the caller's open transaction; return its ``cev_`` ref.

    With ``audit`` (an ``AuditTx``), the same transaction appends the bound chain event
    ``campaign_event`` whose subject is this row's ref and complete-row digest.
    """
    if not conn.in_transaction:
        raise RuntimeError("events are appended inside the state change's transaction")
    if event_type not in EVENT_TYPES:
        raise ValueError("unknown event type")
    if campaign_ref is not None:
        refs.check(campaign_ref, "campaign")
    for key, value in payload.items():
        validator = EVENT_FIELDS.get(key)
        if validator is None or not validator(value):
            raise ValueError("event payload field refused")
    row = {
        "event_ref": refs.mint("event"),
        "campaign_ref": campaign_ref,
        "event_type": event_type,
        "ts": timeutil.iso(now),
        "payload": dict(payload),
    }
    event_digest = journal_digest(row)
    conn.execute(
        "INSERT INTO campaign_events (event_ref, campaign_ref, event_type, ts, payload, event_digest)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            row["event_ref"],
            campaign_ref,
            event_type,
            row["ts"],
            jcs_dumps(row["payload"]).decode(),
            event_digest,
        ),
    )
    if audit is not None:
        audit.append(
            "campaign_event",
            subject_ref=row["event_ref"],
            subject_digest=event_digest,
            payload={"event_type": event_type},
        )
    return str(row["event_ref"])
