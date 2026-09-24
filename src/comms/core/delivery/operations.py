"""Operator and provider operations on sending campaigns (design §6.2, §7.2, §7.3).

Every job-state change goes through the reducer; every operation appends its event
in its own transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from comms.core import refs, timeutil
from comms.core.campaigns.drafts import LifecycleError, load
from comms.core.campaigns.events import ReasonCode, append_event
from comms.core.delivery.freeze import CancelReport
from comms.core.delivery.reducer import PROVIDER_STATUSES, Evidence, reduce
from comms.core.storage.db import write_tx

__all__ = [
    "CancelReport",
    "ProviderDisposition",
    "RetryReport",
    "cancel_sending",
    "record_provider_update",
    "resolve_outcome",
    "retry_failed",
]

ProviderDisposition = Literal[
    "applied", "recorded", "refused", "pending_match", "duplicate", "duplicate_conflict"
]
_TRANSPORTS = frozenset({"telegram", "whatsapp"})


@dataclass(frozen=True)
class RetryReport:
    requeued: int
    retry_exhausted: int


def _generation(conn: Any, campaign: dict[str, Any]) -> tuple[int, str]:
    gen_id = campaign["generation_id"]
    return gen_id, conn.execute("SELECT ref FROM generations WHERE id = ?", (gen_id,)).fetchone()[0]


def cancel_sending(conn: Any, cmp: str, *, now: datetime) -> CancelReport:
    """Cancel every PENDING job by compare-and-set; in-flight and finished jobs are untouched."""
    with write_tx(conn):
        campaign = load(conn, cmp)
        if campaign["lifecycle"] != "SENDING":
            raise LifecycleError("campaign is not sending")
        gen_id, gen = _generation(conn, campaign)
        pending = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM delivery_jobs WHERE generation_id = ? AND state = 'PENDING' ORDER BY id",
                (gen_id,),
            )
        ]
        cancelled = sum(
            reduce(conn, job_id, None, Evidence.CANCEL, None, now=now).disposition == "applied"
            for job_id in pending
        )
        counts = dict(
            conn.execute(
                "SELECT state, count(*) FROM delivery_jobs WHERE generation_id = ? GROUP BY state",
                (gen_id,),
            ).fetchall()
        )
        report = CancelReport(
            cancelled_before_send=cancelled,
            already_sent=counts.get("ACCEPTED", 0) + counts.get("DELIVERED", 0),
            currently_in_flight=counts.get("IN_FLIGHT", 0),
        )
        lifecycle = conn.execute(
            "SELECT lifecycle FROM campaigns WHERE ref = ?", (cmp,)
        ).fetchone()[0]
        append_event(
            conn,
            "campaign.cancelled",
            cmp,
            {
                "generation": gen,
                "lifecycle": lifecycle,
                "cancelled_count": report.cancelled_before_send,
                "already_sent_count": report.already_sent,
                "in_flight_count": report.currently_in_flight,
            },
            now=now,
        )
    return report


def retry_failed(conn: Any, cmp: str, *, now: datetime, cap: int = 5) -> RetryReport:
    """Return provably-unsent (FAILED_TRANSIENT) jobs below the cap to PENDING (§7.2)."""
    with write_tx(conn):
        campaign = load(conn, cmp)
        if campaign["lifecycle"] not in ("SENDING", "COMPLETE"):
            raise LifecycleError("campaign has not been sent")
        gen_id, gen = _generation(conn, campaign)
        failed = conn.execute(
            "SELECT id, attempt_count FROM delivery_jobs WHERE generation_id = ?"
            " AND state = 'FAILED_TRANSIENT' ORDER BY id",
            (gen_id,),
        ).fetchall()
        retryable = [job_id for job_id, count in failed if count < cap]
        if retryable and campaign["lifecycle"] == "COMPLETE":
            conn.execute(
                "UPDATE campaigns SET lifecycle = 'SENDING', updated_at = ? WHERE id = ?",
                (timeutil.iso(now), campaign["id"]),
            )
        requeued = sum(
            reduce(conn, job_id, None, Evidence.RETRY, None, now=now, cap=cap).disposition
            == "applied"
            for job_id in retryable
        )
        report = RetryReport(requeued=requeued, retry_exhausted=len(failed) - len(retryable))
        lifecycle = conn.execute(
            "SELECT lifecycle FROM campaigns WHERE ref = ?", (cmp,)
        ).fetchone()[0]
        append_event(
            conn,
            "campaign.retry_started",
            cmp,
            {
                "generation": gen,
                "lifecycle": lifecycle,
                "requeued_count": report.requeued,
                "exhausted_count": report.retry_exhausted,
            },
            now=now,
        )
    return report


def resolve_outcome(
    conn: Any, job_ref: str, verdict: Literal["sent", "not_sent"], *, now: datetime
) -> None:
    """An operator's statement about an OUTCOME_UNKNOWN job (§7.2)."""
    evidence = {"sent": Evidence.RESOLVE_SENT, "not_sent": Evidence.RESOLVE_NOT_SENT}.get(verdict)
    if evidence is None:
        raise ValueError("verdict must be 'sent' or 'not_sent'")
    try:
        refs.check(job_ref, "job")
    except ValueError:
        raise LifecycleError("unknown job") from None
    with write_tx(conn):
        row = conn.execute(
            "SELECT j.id, c.ref FROM delivery_jobs j JOIN generations g ON g.id = j.generation_id"
            " JOIN campaigns c ON c.id = g.campaign_id WHERE j.ref = ?",
            (job_ref,),
        ).fetchone()
        if row is None:
            raise LifecycleError("unknown job")
        job_id, cmp = row
        if reduce(conn, job_id, None, evidence, None, now=now).disposition != "applied":
            raise LifecycleError("job outcome is not unknown")
        append_event(
            conn, "delivery.outcome_resolved", cmp, {"job": job_ref, "verdict": verdict}, now=now
        )


def _campaign_of_job(conn: Any, job_id: int) -> str:
    return conn.execute(
        "SELECT c.ref FROM delivery_jobs j JOIN generations g ON g.id = j.generation_id"
        " JOIN campaigns c ON c.id = g.campaign_id WHERE j.id = ?",
        (job_id,),
    ).fetchone()[0]


def record_provider_update(
    conn: Any,
    transport: str,
    provider_event_ref: str,
    provider_message_ref: str,
    status: str,
    *,
    now: datetime,
) -> ProviderDisposition:
    """Apply one provider status report, idempotently on (transport, provider_event_ref) (§7.3)."""
    with write_tx(conn):
        return apply_provider_update(
            conn, transport, provider_event_ref, provider_message_ref, status, now=now
        )


def apply_provider_update(
    conn: Any,
    transport: str,
    provider_event_ref: str,
    provider_message_ref: str,
    status: str,
    *,
    now: datetime,
) -> ProviderDisposition:
    """``record_provider_update`` inside the caller's transaction (the webhook worker commits
    the status effect with its flag, C29)."""
    if not conn.in_transaction:
        raise RuntimeError("apply_provider_update needs an open transaction")
    if transport not in _TRANSPORTS or status not in PROVIDER_STATUSES:
        raise ValueError("provider update refused")
    for value in (provider_event_ref, provider_message_ref):
        if not isinstance(value, str) or not 0 < len(value) <= 256:
            raise ValueError("provider update refused")
    stamp = timeutil.iso(now)
    existing = conn.execute(
        "SELECT provider_message_ref, reported_status, job_id FROM provider_events"
        " WHERE transport = ? AND provider_event_ref = ?",
        (transport, provider_event_ref),
    ).fetchone()
    if existing is not None:
        if (existing[0], existing[1]) == (provider_message_ref, status):
            return "duplicate"
        cmp = _campaign_of_job(conn, existing[2]) if existing[2] is not None else None
        append_event(
            conn,
            "delivery.provider_update_refused",
            cmp,
            {
                "transport": transport,
                "reason": ReasonCode.DUPLICATE_CONFLICT.value,
                "status": status,
            },
            now=now,
        )
        return "duplicate_conflict"
    matches = conn.execute(
        "SELECT a.id, a.job_id FROM delivery_attempts a JOIN delivery_jobs j ON j.id = a.job_id"
        " WHERE j.transport = ? AND a.provider_message_ref = ?",
        (transport, provider_message_ref),
    ).fetchall()
    insert = (
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref,"
        " job_id, attempt_id, reported_status, disposition, received_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    if not matches:
        conn.execute(
            insert,
            (
                transport,
                provider_event_ref,
                provider_message_ref,
                None,
                None,
                status,
                "pending_match",
                stamp,
            ),
        )
        return "pending_match"
    if len(matches) > 1:
        conn.execute(
            insert,
            (
                transport,
                provider_event_ref,
                provider_message_ref,
                None,
                None,
                status,
                "refused",
                stamp,
            ),
        )
        append_event(
            conn,
            "delivery.provider_update_refused",
            None,
            {
                "transport": transport,
                "reason": ReasonCode.AMBIGUOUS_MATCH.value,
                "status": status,
            },
            now=now,
        )
        return "refused"
    attempt_id, job_id = matches[0]
    t = reduce(conn, job_id, attempt_id, Evidence.PROVIDER, status, now=now)
    disposition = t.disposition
    conn.execute(
        insert,
        (
            transport,
            provider_event_ref,
            provider_message_ref,
            job_id,
            attempt_id,
            status,
            disposition,
            stamp,
        ),
    )
    return cast(ProviderDisposition, disposition)
