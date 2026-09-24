"""Freeze: send and schedule create an immutable generation of deduplicated jobs in
one transaction; unschedule and pre-send cancel retire it (design §5.1, §5.4, §6.2,
§7.1, §8).

``prepare`` runs inside the transaction and is pure (S3); nothing is delivered here.
Every job-state change goes through the reducer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.core import domains, refs, timeutil
from comms.core.campaigns.drafts import LifecycleError, load, targets_of
from comms.core.campaigns.events import append_event
from comms.core.campaigns.resolve import Candidate, Targets, resolve_targets
from comms.core.canonical import jcs_dumps
from comms.core.delivery.reducer import Evidence, reduce
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryTransport,
    PreparedPayload,
    Skip,
    SkipReason,
)
from comms.core.storage.db import write_tx

__all__ = [
    "CancelReport",
    "NoEligibleEndpoints",
    "cancel",
    "idempotency_key",
    "recipient_digest",
    "schedule",
    "send",
    "snapshot_digest",
    "target_digest",
    "unschedule",
]

_TARGET_KEYS = ("audiences", "destinations", "locations", "recipients")
_TARGET_KIND = {
    "audiences": "audience",
    "destinations": "destination",
    "locations": "location",
    "recipients": "recipient",
}


class NoEligibleEndpoints(LifecycleError):
    """Freeze found no endpoint that would be PENDING (R8)."""


@dataclass(frozen=True)
class CancelReport:
    cancelled_before_send: int
    already_sent: int
    currently_in_flight: int


def _sha(domain: bytes, value: Any) -> str:
    return hashlib.sha256(domain + jcs_dumps(value)).hexdigest()


def idempotency_key(cmp: str, gen: str, transport: str, identity: str) -> str:
    """§7.1: one key per (campaign, generation, transport, delivery identity), for all time."""
    return _sha(
        domains.IDEMPOTENCY,
        {"campaign": cmp, "delivery_identity": identity, "generation": gen, "transport": transport},
    )


def snapshot_digest(
    *,
    cmp: str,
    gen: str,
    send_at: str,
    content: Mapping[str, Any],
    transports: Sequence[str],
    jobs: Sequence[Mapping[str, Any]],
) -> str:
    """§5.4 exactly. Kept in the encrypted generation row; never placed in an event."""
    ordered = sorted(
        (
            {
                k: job[k]
                for k in (
                    "transport",
                    "delivery_identity",
                    "payload_digest",
                    "initial_state",
                    "skip_reason",
                )
            }
            for job in jobs
        ),
        key=lambda job: (job["transport"], job["delivery_identity"]),
    )
    return _sha(
        domains.SNAPSHOT,
        {
            "campaign": cmp,
            "content_digest": hashlib.sha256(jcs_dumps(dict(content))).hexdigest(),
            "generation": gen,
            "jobs": ordered,
            "send_at": send_at,
            "transports": sorted(transports),
        },
    )


def target_digest(targets: Targets, transports: frozenset[str]) -> str:
    """What the operator selected (§9, S5). Opaque refs only."""
    shaped = {}
    for key in _TARGET_KEYS:
        values = sorted(targets.get(key, ()))
        for ref in values:
            refs.check(ref, _TARGET_KIND[key])
        shaped[key] = values
    if not set(transports) <= {"telegram", "whatsapp"}:
        raise ValueError("unknown transport")
    return _sha(domains.TARGET, {"targets": shaped, "transports": sorted(transports)})


def recipient_digest(jobs: Sequence[tuple[str, str, tuple[str, ...], str]]) -> str:
    """What freeze resolved (§9, S5): (job ref, transport, endpoint refs, state). Opaque refs only."""
    entries = []
    for job_ref, transport, endpoints, state in jobs:
        refs.check(job_ref, "job")
        for ref in endpoints:
            if refs.kind_of(ref) not in ("destination", "contact_point"):
                raise ValueError("unexpected ref")
        if transport not in ("telegram", "whatsapp"):
            raise ValueError("unknown transport")
        entries.append(
            {"endpoints": sorted(endpoints), "job": job_ref, "state": state, "transport": transport}
        )
    return _sha(domains.RECIPIENTS, sorted(entries, key=lambda e: e["job"]))


@dataclass(frozen=True)
class _Planned:
    candidate: Candidate
    identity: str
    job_ref: str
    key: str
    payload: PreparedPayload | None
    skip: SkipReason | None

    @property
    def state(self) -> str:
        return "PENDING" if self.payload is not None else "SKIPPED_PLATFORM_POLICY"


def _freeze(
    conn: Any,
    cmp: str,
    transports: Mapping[str, DeliveryTransport],
    *,
    now: datetime,
    send_at: datetime,
    lifecycle: str,
) -> str:
    stamp, at = timeutil.iso(now), timeutil.iso(send_at)
    with write_tx(conn):
        campaign = load(conn, cmp)
        if campaign["lifecycle"] != "READY":
            raise LifecycleError("campaign is not ready")
        targets, wanted = targets_of(campaign)
        if not wanted <= set(transports):
            raise LifecycleError("transport unavailable")
        content = json.loads(campaign["content"])
        gen = refs.mint("generation")
        planned: list[_Planned] = []
        for candidate in resolve_targets(conn, targets, wanted):
            identity = conn.execute(
                "SELECT identity FROM delivery_identities WHERE id = ?", (candidate.identity_id,)
            ).fetchone()[0]
            prepared = transports[candidate.transport].prepare(
                DeliveryIntent(candidate.transport, identity, content), timeutil.utc(send_at)
            )
            if isinstance(prepared, Skip):
                payload, skip = None, SkipReason(prepared.reason)
            elif isinstance(prepared, PreparedPayload):
                if hashlib.sha256(prepared.data).hexdigest() != prepared.digest:
                    raise ValueError("payload digest does not match its data")
                payload, skip = prepared, None
            else:
                raise TypeError("prepare returned an unknown type")
            planned.append(
                _Planned(
                    candidate,
                    identity,
                    refs.mint("job"),
                    idempotency_key(cmp, gen, candidate.transport, identity),
                    payload,
                    skip,
                )
            )
        if not any(p.payload is not None for p in planned):
            raise NoEligibleEndpoints("NO_ELIGIBLE_ENDPOINTS")
        digest = snapshot_digest(
            cmp=cmp,
            gen=gen,
            send_at=at,
            content=content,
            transports=sorted(wanted),
            jobs=[
                {
                    "transport": p.candidate.transport,
                    "delivery_identity": p.identity,
                    "payload_digest": p.payload.digest if p.payload else None,
                    "initial_state": p.state,
                    "skip_reason": p.skip.value if p.skip else None,
                }
                for p in planned
            ],
        )
        gen_id = conn.execute(
            "INSERT INTO generations (ref, campaign_id, created_at, send_at, content,"
            " snapshot_digest, status) VALUES (?, ?, ?, ?, ?, ?, 'active')",
            (gen, campaign["id"], stamp, at, jcs_dumps(content).decode(), digest),
        ).lastrowid
        for p in planned:
            job_id = conn.execute(
                "INSERT INTO delivery_jobs (ref, generation_id, transport, identity_id,"
                " idempotency_key, payload, payload_digest, skip_reason, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p.job_ref,
                    gen_id,
                    p.candidate.transport,
                    p.candidate.identity_id,
                    p.key,
                    p.payload.data if p.payload else None,
                    p.payload.digest if p.payload else None,
                    p.skip.value if p.skip else None,
                    p.state,
                ),
            ).lastrowid
            for origin in p.candidate.origins:
                conn.execute(
                    "INSERT INTO job_origins (job_id, endpoint_ref, path) VALUES (?, ?, ?)",
                    (job_id, origin.endpoint_ref, jcs_dumps(list(origin.path)).decode()),
                )
        conn.execute(
            "UPDATE campaigns SET current_generation_id = ?, summary = 'IN_PROGRESS',"
            " lifecycle = ?, updated_at = ? WHERE id = ?",
            (gen_id, lifecycle, stamp, campaign["id"]),
        )
        event_payload: dict[str, Any] = {
            "generation": gen,
            "lifecycle": lifecycle,
            "job_count": len(planned),
            "pending_count": sum(p.payload is not None for p in planned),
            "skipped_count": sum(p.payload is None for p in planned),
            "target_digest": target_digest(targets, wanted),
            "recipient_digest": recipient_digest(
                [
                    (p.job_ref, p.candidate.transport, p.candidate.endpoint_refs, p.state)
                    for p in planned
                ]
            ),
        }
        if lifecycle == "SCHEDULED":
            event_payload["send_at"] = at
        event = "campaign.send_started" if lifecycle == "SENDING" else "campaign.scheduled"
        append_event(conn, event, cmp, event_payload, now=now)
    return gen


def send(conn: Any, cmp: str, transports: Mapping[str, DeliveryTransport], *, now: datetime) -> str:
    """READY → SENDING with a new generation whose send time is now."""
    return _freeze(conn, cmp, transports, now=now, send_at=now, lifecycle="SENDING")


def schedule(
    conn: Any, cmp: str, at: datetime, transports: Mapping[str, DeliveryTransport], *, now: datetime
) -> str:
    """READY → SCHEDULED; jobs and content are frozen now, for ``at`` (UTC)."""
    return _freeze(conn, cmp, transports, now=now, send_at=timeutil.utc(at), lifecycle="SCHEDULED")


def _retire_scheduled(conn: Any, campaign: Mapping[str, Any], *, now: datetime) -> tuple[str, int]:
    """Cancel the PENDING jobs of the scheduled generation (S8), discard it, clear the pointer."""
    gen_id = campaign["generation_id"]
    gen = conn.execute("SELECT ref FROM generations WHERE id = ?", (gen_id,)).fetchone()[0]
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
    conn.execute(
        "UPDATE campaigns SET current_generation_id = NULL, summary = NULL, updated_at = ?"
        " WHERE id = ?",
        (timeutil.iso(now), campaign["id"]),
    )
    conn.execute("UPDATE generations SET status = 'discarded' WHERE id = ?", (gen_id,))
    return gen, cancelled


def unschedule(conn: Any, cmp: str, *, now: datetime) -> None:
    """SCHEDULED → READY: the generation is discarded and never reused (§8, R6)."""
    with write_tx(conn):
        campaign = load(conn, cmp)
        if campaign["lifecycle"] != "SCHEDULED":
            raise LifecycleError("campaign is not scheduled")
        gen, cancelled = _retire_scheduled(conn, campaign, now=now)
        conn.execute("UPDATE campaigns SET lifecycle = 'READY' WHERE id = ?", (campaign["id"],))
        append_event(
            conn,
            "campaign.unscheduled",
            cmp,
            {"generation": gen, "cancelled_count": cancelled, "lifecycle": "READY"},
            now=now,
        )


def cancel(conn: Any, cmp: str, *, now: datetime) -> CancelReport:
    """Cancel (§6.2). A SENDING campaign is cancelled job by job, by ``operations``."""
    if load(conn, cmp)["lifecycle"] == "SENDING":
        from comms.core.delivery.operations import (
            cancel_sending,
        )

        return cancel_sending(conn, cmp, now=now)
    with write_tx(conn):
        campaign = load(conn, cmp)
        lifecycle = campaign["lifecycle"]
        if lifecycle not in ("DRAFT", "READY", "SCHEDULED"):
            raise LifecycleError("campaign cannot be cancelled")
        cancelled = 0
        payload: dict[str, Any] = {"lifecycle": "CANCELLED"}
        if lifecycle == "SCHEDULED":
            gen, cancelled = _retire_scheduled(conn, campaign, now=now)
            payload.update(generation=gen, cancelled_count=cancelled)
        conn.execute(
            "UPDATE campaigns SET lifecycle = 'CANCELLED', updated_at = ? WHERE id = ?",
            (timeutil.iso(now), campaign["id"]),
        )
        append_event(conn, "campaign.cancelled", cmp, payload, now=now)
    return CancelReport(cancelled_before_send=cancelled, already_sent=0, currently_in_flight=0)
