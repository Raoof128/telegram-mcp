"""The one reducer: every write of ``delivery_jobs.state`` goes through :func:`reduce`
(design §6.4, §7.3, R4). The delivery summary is re-derived in the same transaction
as every job change (§6.3, R7), and the lifecycle reaches ``COMPLETE`` in the
transaction of the last job change.

``decide`` is pure. ``reduce`` applies its decision as a compare-and-set on the state
``decide`` saw, so a concurrent writer can only make it refuse, never regress.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from comms.core import refs, timeutil
from comms.core.campaigns.events import ReasonCode, append_event

__all__ = [
    "NAMED_DESCENTS",
    "RANK",
    "TERMINAL",
    "Evidence",
    "Transition",
    "bind_provider_ref",
    "complete_if_idle",
    "decide",
    "reduce",
    "refresh_summary",
    "summarize",
]


class Evidence(StrEnum):
    CLAIM = "CLAIM"
    RESULT = "RESULT"
    PROVIDER = "PROVIDER"
    RESOLVE_SENT = "RESOLVE_SENT"
    RESOLVE_NOT_SENT = "RESOLVE_NOT_SENT"
    CANCEL = "CANCEL"
    SKIP_REVALIDATION = "SKIP_REVALIDATION"
    RETRY = "RETRY"
    RECOVER = "RECOVER"


@dataclass(frozen=True)
class Transition:
    new_state: str | None
    attempt_outcome: str | None
    disposition: str  # applied / recorded / refused
    attempt_id: int | None = None


RANK = {
    "PENDING": 0,
    "IN_FLIGHT": 1,
    "FAILED_TRANSIENT": 2,
    "OUTCOME_UNKNOWN": 2,
    "ACCEPTED": 3,
    "DELIVERED": 4,
}
TERMINAL = frozenset(
    {"FAILED_PERMANENT", "CANCELLED", "SKIPPED_PLATFORM_POLICY", "SKIPPED_REVALIDATION"}
)
SUCCESS = frozenset({"ACCEPTED", "DELIVERED"})
ACTIVE = frozenset({"PENDING", "IN_FLIGHT"})
RESULT_KINDS = frozenset(
    {"ACCEPTED", "DELIVERED", "FAILED_TRANSIENT", "FAILED_PERMANENT", "OUTCOME_UNKNOWN"}
)
PROVIDER_STATUSES = frozenset({"ACCEPTED", "DELIVERED", "FAILED_PERMANENT"})
# The only applied transitions that lower RANK or leave a success (G3); each has a
# precondition: RETRY below the attempt cap; PROVIDER failure for the current attempt.
NAMED_DESCENTS = frozenset(
    {("FAILED_TRANSIENT", "RETRY", "PENDING"), ("ACCEPTED", "PROVIDER", "FAILED_PERMANENT")}
)

_REFUSED = Transition(None, None, "refused")


def decide(
    job_state: str,
    *,
    evidence: Evidence,
    value: str | None,
    is_current_attempt: bool,
    attempt_count: int = 0,
    cap: int = 5,
) -> Transition:
    """What ``evidence`` does to a job in ``job_state``. Pure; the full table is tested."""
    ev = Evidence(evidence)
    if ev is Evidence.PROVIDER:
        if value not in PROVIDER_STATUSES:
            return _REFUSED
        if value in SUCCESS:
            if job_state in RANK and RANK[value] > RANK[job_state]:
                return Transition(value, value, "applied")
            return Transition(None, value, "recorded")
        if is_current_attempt and job_state in ("IN_FLIGHT", "ACCEPTED", "OUTCOME_UNKNOWN"):
            return Transition("FAILED_PERMANENT", value, "applied")
        return Transition(None, value, "recorded")
    if ev is Evidence.RESULT:
        if value not in RESULT_KINDS or not is_current_attempt:
            return _REFUSED
        if job_state == "IN_FLIGHT":
            return Transition(value, value, "applied")
        if job_state in SUCCESS:  # the provider's own report got there first
            return Transition(None, value, "recorded")
        return _REFUSED
    if value is not None:
        return _REFUSED
    if ev is Evidence.CLAIM and job_state == "PENDING":
        return Transition("IN_FLIGHT", None, "applied")
    if ev is Evidence.CANCEL and job_state == "PENDING":
        return Transition("CANCELLED", None, "applied")
    if ev is Evidence.SKIP_REVALIDATION and job_state == "PENDING":
        return Transition("SKIPPED_REVALIDATION", None, "applied")
    if ev is Evidence.RETRY and job_state == "FAILED_TRANSIENT" and attempt_count < cap:
        return Transition("PENDING", None, "applied")
    if ev is Evidence.RECOVER and job_state == "IN_FLIGHT" and is_current_attempt:
        return Transition("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN", "applied")
    if ev is Evidence.RESOLVE_SENT and job_state == "OUTCOME_UNKNOWN":
        return Transition("ACCEPTED", None, "applied")
    if ev is Evidence.RESOLVE_NOT_SENT and job_state == "OUTCOME_UNKNOWN":
        return Transition("FAILED_TRANSIENT", None, "applied")
    return _REFUSED


def summarize(states: Sequence[str], any_attempt_ever: bool) -> str:
    """The delivery summary of a generation's jobs (§6.3). Defined only on a non-empty set."""
    if not states:
        raise ValueError("a generation always has jobs")
    if any(s in ACTIVE for s in states):
        return "IN_PROGRESS"
    if "OUTCOME_UNKNOWN" in states:
        return "INDETERMINATE"
    successes = sum(1 for s in states if s in SUCCESS)
    if successes == len(states):
        return "SENT"
    if successes:
        return "PARTIAL"
    if "CANCELLED" in states and not any_attempt_ever:
        return "CANCELLED"
    return "FAILED"


def _require_tx(conn: Any) -> None:
    if not conn.in_transaction:
        raise RuntimeError("job state is written only inside a comms.db transaction")


def _campaign_of_generation(
    conn: Any, generation_id: int
) -> tuple[int, str, str, str | None, int | None]:
    row = conn.execute(
        "SELECT c.id, c.ref, c.lifecycle, c.summary, c.current_generation_id FROM generations g"
        " JOIN campaigns c ON c.id = g.campaign_id WHERE g.id = ?",
        (generation_id,),
    ).fetchone()
    return int(row[0]), str(row[1]), str(row[2]), row[3], row[4]


def _counts(conn: Any, generation_id: int) -> dict[str, int]:
    states = [
        r[0]
        for r in conn.execute(
            "SELECT state FROM delivery_jobs WHERE generation_id = ?", (generation_id,)
        )
    ]
    return {
        "job_count": len(states),
        "success_count": sum(s in SUCCESS for s in states),
        "failure_count": sum(s in ("FAILED_TRANSIENT", "FAILED_PERMANENT") for s in states),
        "unknown_count": sum(s == "OUTCOME_UNKNOWN" for s in states),
        "skipped_count": sum(s.startswith("SKIPPED") for s in states),
        "cancelled_count": sum(s == "CANCELLED" for s in states),
    }


def refresh_summary(conn: Any, campaign_id: int, *, now: datetime) -> str | None:
    """Re-derive and store the summary of the campaign's current generation."""
    _require_tx(conn)
    row = conn.execute(
        "SELECT ref, lifecycle, summary, current_generation_id FROM campaigns WHERE id = ?",
        (campaign_id,),
    ).fetchone()
    cmp, lifecycle, old, generation_id = row
    if generation_id is None:
        return None
    states = [
        r[0]
        for r in conn.execute(
            "SELECT state FROM delivery_jobs WHERE generation_id = ?", (generation_id,)
        )
    ]
    attempted = (
        conn.execute(
            "SELECT 1 FROM delivery_attempts a JOIN delivery_jobs j ON j.id = a.job_id"
            " WHERE j.generation_id = ? LIMIT 1",
            (generation_id,),
        ).fetchone()
        is not None
    )
    new = summarize(states, attempted)
    if new != old:
        conn.execute(
            "UPDATE campaigns SET summary = ?, updated_at = ? WHERE id = ?",
            (new, timeutil.iso(now), campaign_id),
        )
        if lifecycle == "COMPLETE":
            append_event(conn, "campaign.summary_changed", cmp, {"summary": new}, now=now)
    return new


def complete_if_idle(conn: Any, campaign_id: int, *, now: datetime) -> bool:
    """SENDING → COMPLETE once no job of the current generation is PENDING or IN_FLIGHT."""
    _require_tx(conn)
    row = conn.execute(
        "SELECT ref, lifecycle, current_generation_id FROM campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    cmp, lifecycle, generation_id = row
    if lifecycle != "SENDING" or generation_id is None:
        return False
    active = conn.execute(
        "SELECT 1 FROM delivery_jobs WHERE generation_id = ? AND state IN ('PENDING','IN_FLIGHT')"
        " LIMIT 1",
        (generation_id,),
    ).fetchone()
    if active is not None:
        return False
    summary = refresh_summary(conn, campaign_id, now=now)
    conn.execute(
        "UPDATE campaigns SET lifecycle = 'COMPLETE', updated_at = ? WHERE id = ?",
        (timeutil.iso(now), campaign_id),
    )
    generation_ref = conn.execute(
        "SELECT ref FROM generations WHERE id = ?", (generation_id,)
    ).fetchone()[0]
    append_event(
        conn,
        "campaign.completed",
        cmp,
        {"generation": generation_ref, "summary": summary, **_counts(conn, generation_id)},
        now=now,
    )
    return True


def reduce(
    conn: Any,
    job_id: int,
    attempt_id: int | None,
    evidence: Evidence,
    value: str | None,
    *,
    now: datetime,
    cap: int = 5,
) -> Transition:
    """Apply ``evidence`` to one job: the only writer of ``delivery_jobs.state``."""
    _require_tx(conn)
    ev = Evidence(evidence)
    stamp = timeutil.iso(now)
    row = conn.execute(
        "SELECT state, attempt_count, generation_id, transport FROM delivery_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown job")
    state, attempt_count, generation_id, transport = row
    attempt_no = None
    if attempt_id is not None:
        found = conn.execute(
            "SELECT attempt_no FROM delivery_attempts WHERE id = ? AND job_id = ?",
            (attempt_id, job_id),
        ).fetchone()
        if found is None:
            raise ValueError("attempt does not belong to job")
        attempt_no = int(found[0])
    elif ev in (Evidence.RESULT, Evidence.PROVIDER, Evidence.RECOVER) and attempt_count:
        attempt_id, attempt_no = conn.execute(
            "SELECT id, attempt_no FROM delivery_attempts WHERE job_id = ? AND attempt_no = ?",
            (job_id, attempt_count),
        ).fetchone()
    current = attempt_no is None or attempt_no == attempt_count
    t = decide(
        state,
        evidence=ev,
        value=value,
        is_current_attempt=current,
        attempt_count=attempt_count,
        cap=cap,
    )
    campaign_id, cmp, _lifecycle, _summary, _current_gen = _campaign_of_generation(
        conn, generation_id
    )

    if t.disposition == "applied":
        if ev is Evidence.CLAIM:
            cur = conn.execute(
                "UPDATE delivery_jobs SET state = 'IN_FLIGHT', attempt_count = attempt_count + 1"
                " WHERE id = ? AND state = 'PENDING' AND EXISTS (SELECT 1 FROM campaigns c"
                " WHERE c.id = ? AND c.lifecycle = 'SENDING' AND c.current_generation_id = ?)",
                (job_id, campaign_id, generation_id),
            )
            if cur.rowcount != 1:
                return _REFUSED
            new_attempt = conn.execute(
                "INSERT INTO delivery_attempts (ref, job_id, attempt_no, started_at)"
                " VALUES (?, ?, ?, ?)",
                (refs.mint("attempt"), job_id, attempt_count + 1, stamp),
            ).lastrowid
            t = Transition("IN_FLIGHT", None, "applied", attempt_id=int(new_attempt))
        else:
            cur = conn.execute(
                "UPDATE delivery_jobs SET state = ? WHERE id = ? AND state = ?",
                (t.new_state, job_id, state),
            )
            if cur.rowcount != 1:
                return _REFUSED
    if t.attempt_outcome is not None and attempt_id is not None and t.disposition != "refused":
        conn.execute(
            "UPDATE delivery_attempts SET outcome = ?, finished_at = coalesce(finished_at, ?)"
            " WHERE id = ? AND (outcome IS NULL OR outcome <> 'DELIVERED')",
            (t.attempt_outcome, stamp, attempt_id),
        )
    if ev is Evidence.PROVIDER and t.disposition != "applied":
        reason = (
            ReasonCode.NOT_CURRENT_ATTEMPT
            if value == "FAILED_PERMANENT" and not current
            else ReasonCode.REDUCER_REFUSED
        )
        payload: dict[str, Any] = {"transport": transport, "reason": reason.value}
        if value in PROVIDER_STATUSES:
            payload["status"] = value
        append_event(conn, "delivery.provider_update_refused", cmp, payload, now=now)
    if t.disposition != "applied":
        return t
    if state in ACTIVE and t.new_state not in ACTIVE:
        still = conn.execute(
            "SELECT 1 FROM delivery_jobs WHERE generation_id = ? AND transport = ?"
            " AND state IN ('PENDING','IN_FLIGHT') LIMIT 1",
            (generation_id, transport),
        ).fetchone()
        if still is None:
            generation_ref = conn.execute(
                "SELECT ref FROM generations WHERE id = ?", (generation_id,)
            ).fetchone()[0]
            append_event(
                conn,
                "campaign.transport_completed",
                cmp,
                {"generation": generation_ref, "transport": transport},
                now=now,
            )
    if _current_gen == generation_id:
        refresh_summary(conn, campaign_id, now=now)
        complete_if_idle(conn, campaign_id, now=now)
    return t


def bind_provider_ref(
    conn: Any, attempt_id: int, provider_message_ref: str, *, now: datetime
) -> int:
    """Bind the provider's message reference to an attempt, then reconcile every
    ``pending_match`` update for it through the reducer, in arrival order (S4)."""
    _require_tx(conn)
    if not isinstance(provider_message_ref, str) or not provider_message_ref:
        raise ValueError("provider reference refused")
    cur = conn.execute(
        "UPDATE delivery_attempts SET provider_message_ref = ?"
        " WHERE id = ? AND provider_message_ref IS NULL",
        (provider_message_ref, attempt_id),
    )
    if cur.rowcount != 1:
        raise ValueError("provider reference already bound")
    job_id, transport = conn.execute(
        "SELECT j.id, j.transport FROM delivery_attempts a JOIN delivery_jobs j ON j.id = a.job_id"
        " WHERE a.id = ?",
        (attempt_id,),
    ).fetchone()
    matches = conn.execute(
        "SELECT count(*) FROM delivery_attempts a JOIN delivery_jobs j ON j.id = a.job_id"
        " WHERE j.transport = ? AND a.provider_message_ref = ?",
        (transport, provider_message_ref),
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT id, reported_status FROM provider_events WHERE transport = ?"
        " AND provider_message_ref = ? AND disposition = 'pending_match' ORDER BY id",
        (transport, provider_message_ref),
    ).fetchall()
    for event_id, status in pending:
        if matches != 1:
            conn.execute(
                "UPDATE provider_events SET disposition = 'refused' WHERE id = ?", (event_id,)
            )
            continue
        t = reduce(conn, job_id, attempt_id, Evidence.PROVIDER, status, now=now)
        conn.execute(
            "UPDATE provider_events SET disposition = ?, job_id = ?, attempt_id = ? WHERE id = ?",
            (t.disposition, job_id, attempt_id, event_id),
        )
    return len(pending)
