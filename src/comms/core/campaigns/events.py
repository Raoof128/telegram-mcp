"""The campaign event log: an append-only operational journal inside the encrypted
comms.db. It is not the tamper-evident audit chain (design §9, H4); 5c integrates it.

Every event is appended in the transaction of the state change it records (R15).
Payload privacy is enforced by type, not by string shape (S6): each key has a fixed
validator over a finite domain, and nothing else is accepted.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from comms.core import refs, timeutil
from comms.core.canonical import jcs_dumps

__all__ = ["EVENT_FIELDS", "EVENT_TYPES", "LIFECYCLES", "SUMMARIES", "ReasonCode", "append_event"]

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


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
Validator = Callable[[Any], bool]


def _ref(kind: str) -> Validator:
    def check(value: Any) -> bool:
        try:
            refs.check(value, kind)
        except ValueError:
            return False
        return True

    return check


def _one_of(values: frozenset[str] | set[str]) -> Validator:
    return lambda value: isinstance(value, str) and value in values


def _count(minimum: int) -> Validator:
    return lambda value: type(value) is int and value >= minimum


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _time(value: Any) -> bool:
    try:
        timeutil.parse(value)
    except ValueError:
        return False
    return True


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
    "generation": _ref("generation"),
    "job": _ref("job"),
    "attempt": _ref("attempt"),
    "transport": _one_of({"telegram", "whatsapp"}),
    "target_digest": _digest,
    "recipient_digest": _digest,
    "snapshot_digest": _digest,
    **{name: _count(0) for name in _COUNTS},
    "attempt_no": _count(1),
    "summary": _one_of(SUMMARIES),
    "lifecycle": _one_of(LIFECYCLES),
    "reason": _one_of({r.value for r in ReasonCode}),
    "send_at": _time,
    "verdict": _one_of({"sent", "not_sent"}),
    "status": _one_of({"ACCEPTED", "DELIVERED", "FAILED_PERMANENT"}),
}


def append_event(
    conn: Any,
    event_type: str,
    campaign_ref: str | None,
    payload: Mapping[str, Any],
    *,
    now: datetime,
) -> str:
    """Append one event inside the caller's open transaction; return its ``cev_`` ref."""
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
    ref = refs.mint("event")
    conn.execute(
        "INSERT INTO campaign_events (event_ref, campaign_ref, event_type, ts, payload)"
        " VALUES (?, ?, ?, ?, ?)",
        (ref, campaign_ref, event_type, timeutil.iso(now), jcs_dumps(dict(payload)).decode()),
    )
    return ref
