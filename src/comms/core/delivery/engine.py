"""The delivery engine: the external-effect boundary (design §5.2, §5.3).

Per job: claim in one transaction (revalidate, then compare-and-set with its attempt),
``deliver`` with no transaction open, then record the result in a second transaction.
Only an exception raised by ``deliver`` becomes ``OUTCOME_UNKNOWN`` (S7); every other
exception propagates and fails closed. Only the executor-lease holder may execute.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.campaigns.drafts import LifecycleError, load
from comms.core.campaigns.resolve import path_is_valid
from comms.core.delivery.reducer import Evidence, bind_provider_ref, reduce
from comms.core.delivery.transport import (
    DeliveryResult,
    DeliveryTransport,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
)
from comms.core.storage.db import io_guard, write_tx

__all__ = [
    "CRASH_POINTS",
    "Engine",
    "EngineCrash",
    "ExecutionReport",
    "ExecutorLease",
    "SupportsHeld",
    "require_lease",
]

CRASH_POINTS = ("after_claim", "during_deliver", "after_deliver_before_record")


class SupportsHeld(Protocol):
    def held(self) -> bool: ...


class ExecutorLease:
    """Proof of holding the single-runtime lock (R20); 5e passes the runtime lock."""

    def __init__(self, lock: SupportsHeld) -> None:
        self._lock = lock

    def held(self) -> bool:
        return bool(self._lock.held())


def require_lease(lease: object) -> None:
    if not isinstance(lease, ExecutorLease) or not lease.held():
        raise PermissionError("executor lease required")


class EngineCrash(BaseException):
    """Raised only by the ``crash_at`` seam. BaseException, so no handler swallows it."""


@dataclass(frozen=True)
class ExecutionReport:
    claimed: int = 0
    skipped_revalidation: int = 0
    outcomes: Mapping[str, int] = field(default_factory=dict)


class Engine:
    def __init__(
        self,
        conn: Any,
        transports: Mapping[str, DeliveryTransport],
        *,
        clock: Callable[[], datetime],
        crash_at: str | None = None,
    ) -> None:
        if crash_at is not None and crash_at not in CRASH_POINTS:
            raise ValueError("unknown crash point")
        self._conn = conn
        self._transports = dict(transports)
        self._clock = clock
        self._crash_at = crash_at

    def _now(self) -> datetime:
        return timeutil.utc(self._clock())

    def _crash(self, point: str) -> None:
        if self._crash_at == point:
            raise EngineCrash(point)

    def execute(self, lease: ExecutorLease, cmp: str) -> ExecutionReport:
        """Deliver every PENDING job of the campaign's current generation, transport by transport."""
        require_lease(lease)
        conn = self._conn
        campaign = load(conn, cmp)
        generation_id = campaign["generation_id"]
        if campaign["lifecycle"] != "SENDING" or generation_id is None:
            return ExecutionReport()
        pending = conn.execute(
            "SELECT id, transport FROM delivery_jobs WHERE generation_id = ? AND state = 'PENDING'"
            " ORDER BY transport, id",
            (generation_id,),
        ).fetchall()
        if not {t for _, t in pending} <= set(self._transports):
            raise LifecycleError("transport unavailable")
        generation_ref = conn.execute(
            "SELECT ref FROM generations WHERE id = ?", (generation_id,)
        ).fetchone()[0]
        claimed = skipped = 0
        outcomes: Counter[str] = Counter()
        for job_id, transport_name in pending:
            transport = self._transports[transport_name]
            frozen = self._claim(job_id, generation_ref, transport)
            if not isinstance(frozen, tuple):
                skipped += frozen == "skipped"
                continue
            delivery, attempt_id = frozen
            claimed += 1
            self._crash("after_claim")
            io_guard(conn)
            self._crash("during_deliver")
            try:
                result = transport.deliver(delivery)
            except Exception:  # noqa: BLE001 -- S7: the ONE boundary where an exception is an outcome
                result = DeliveryResult(ResultKind.OUTCOME_UNKNOWN)
            self._crash("after_deliver_before_record")
            with write_tx(conn):
                reduce(conn, job_id, attempt_id, Evidence.RESULT, result.kind, now=self._now())
                if result.provider_message_ref:
                    bind_provider_ref(
                        conn, attempt_id, result.provider_message_ref, now=self._now()
                    )
            outcomes[result.kind.value] += 1
        return ExecutionReport(
            claimed=claimed, skipped_revalidation=skipped, outcomes=dict(outcomes)
        )

    def _claim(
        self, job_id: int, generation_ref: str, transport: DeliveryTransport
    ) -> tuple[FrozenDelivery, int] | str | None:
        """Revalidate and claim in one transaction. Any exception here propagates (S7)."""
        conn = self._conn
        now = self._now()
        with write_tx(conn):
            row = conn.execute(
                "SELECT j.ref, j.state, j.transport, i.identity, j.payload, j.payload_digest,"
                " j.idempotency_key, j.attempt_count FROM delivery_jobs j"
                " JOIN delivery_identities i ON i.id = j.identity_id WHERE j.id = ?",
                (job_id,),
            ).fetchone()
            ref, state, transport_name, identity, data, digest, key, attempt_count = row
            if state != "PENDING":
                return None
            payload = PreparedPayload(data=bytes(data), digest=digest)
            paths = [
                tuple(json.loads(p))
                for (p,) in conn.execute("SELECT path FROM job_origins WHERE job_id = ?", (job_id,))
            ]
            eligible = any(path_is_valid(conn, path) for path in paths)
            if not eligible or transport.still_valid(payload, now) is not True:
                reduce(conn, job_id, None, Evidence.SKIP_REVALIDATION, None, now=now)
                return "skipped"
            t = reduce(conn, job_id, None, Evidence.CLAIM, None, now=now)
            if t.disposition != "applied" or t.attempt_id is None:
                return None
        delivery = FrozenDelivery(
            job_ref=ref,
            generation_ref=generation_ref,
            transport=transport_name,
            identity=identity,
            payload=payload,
            idempotency_key=key,
            attempt_no=attempt_count + 1,
        )
        return delivery, t.attempt_id
