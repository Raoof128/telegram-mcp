"""Startup recovery and the supervised background workers (D39-PRE Task E5).

``startup_recovery`` runs once, before any listener: interrupted admin mutations settle (A19)
and campaigns left mid-delivery are reconciled, never resent (5b-4 recovery).

``Workers`` runs the daemon's loops. Each step is synchronous and runs on the event loop's own
thread, like every MCP and admin handler: the daemon holds one SQLite connection, and a SQLite
connection is used from one thread only. Provider I/O happens outside any transaction (R18).
A step's exception is classified by type in one table (owner amendment):

- **recoverable** (a provider or the network is down, a rate limit, a temporary refusal): logged
  once by a fixed code with no message text (text could hold provider data), then the loop
  backs off, doubling to at most 300 s and resetting on success; the daemon stays up;
- **safety** (audit, key-store or database integrity, a lost update-stream claim, and any type
  not in the table: fail closed): the integrity latch is set, every effect-making loop stops,
  and the listeners keep serving reads while writes answer ``AUDIT_INTEGRITY_DEGRADED``.
  Database corruption also stops the daemon (``DaemonStop``), which exits nonzero.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlcipher3

from comms.core.audit.integrity import is_degraded, latch_degraded
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.delivery.recovery import recover
from comms.core.delivery.scheduling import run_due
from comms.runtime.adapters import Adapters
from comms.runtime.state import CommsState
from comms.services.recovery import recover_mutations
from comms.transports.telegram.bot.http import BotRefused, BotTransportError
from comms.transports.telegram.bot.updates import PollingRefused
from comms.transports.telegram.telegram.deadline import DeadlineExceeded
from comms.transports.whatsapp.cloud.http import GraphRefused, GraphTransportError

__all__ = ["RECOVERABLE", "DaemonStop", "Loop", "Workers", "build_workers", "startup_recovery"]

_logger = logging.getLogger("comms.workers")
MAX_BACKOFF_S = 300.0
RECOVERABLE: tuple[type[BaseException], ...] = (
    BotTransportError,
    BotRefused,
    PollingRefused,
    GraphTransportError,
    GraphRefused,
    DeadlineExceeded,
    TimeoutError,
    ConnectionError,
)
_FATAL: tuple[type[BaseException], ...] = (sqlcipher3.dbapi2.DatabaseError,)


class DaemonStop(Exception):
    """A worker met a failure the daemon cannot run through; the daemon exits nonzero."""


@dataclass(frozen=True)
class Loop:
    name: str
    step: Callable[[], Any]
    every: float
    effects: bool  # starts external effects: stops once the latch is set


def startup_recovery(state: CommsState, lease: ExecutorLease, *, now: datetime) -> dict[str, int]:
    mutations = recover_mutations(state.writer)
    campaigns = recover(lease, state.conn, now=now)
    return {
        "mutations_settled": mutations.settled,
        "mutations_unknown": mutations.unknown,
        "mutations_resumable": mutations.resumable,
        "deliveries_marked_unknown": campaigns.marked_unknown,
        "campaigns_resumable": len(campaigns.resumable),
    }


class Workers:
    def __init__(
        self,
        conn: Any,
        loops: tuple[Loop, ...],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._conn, self.loops, self._clock = conn, loops, clock

    async def _run(self, loop: Loop, stop: asyncio.Event) -> None:
        period = loop.every
        while not stop.is_set():
            if loop.effects and is_degraded(self._conn):
                return  # no new external effect while the trail is degraded (A8)
            try:
                loop.step()
            except RECOVERABLE:
                _logger.warning("worker_failed name=%s class=recoverable", loop.name)
                period = min(period * 2, MAX_BACKOFF_S)
            except _FATAL:
                _logger.error("worker_failed name=%s class=fatal", loop.name)
                raise DaemonStop from None
            except Exception:  # noqa: BLE001 -- anything else is a safety failure: fail closed
                _logger.error("worker_failed name=%s class=safety", loop.name)
                latch_degraded(self._conn, reason="WORKER_SAFETY_FAILURE", now=self._clock())
                if loop.effects:
                    return
                period = min(period * 2, MAX_BACKOFF_S)
            else:
                period = loop.every
            try:
                await asyncio.wait_for(stop.wait(), timeout=period)
            except TimeoutError:
                pass

    async def run(self, stop: asyncio.Event) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                for loop in self.loops:
                    group.create_task(self._run(loop, stop))
        except* DaemonStop:
            raise DaemonStop from None


def _deliver_sending(conn: Any, engine: Engine, lease: ExecutorLease) -> None:
    """Execute every SENDING campaign with pending jobs (a send only freezes; this delivers)."""
    sending = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT c.ref FROM campaigns c JOIN delivery_jobs j"
            " ON j.generation_id = c.current_generation_id"
            " WHERE c.lifecycle = 'SENDING' AND j.state = 'PENDING' ORDER BY c.ref"
        )
    ]
    for cmp in sending:
        engine.execute(lease, cmp)


def build_workers(
    state: CommsState,
    adapters: Adapters,
    lease: ExecutorLease,
    *,
    clock: Callable[[], datetime],
    intervals: Mapping[str, float] | None = None,
) -> Workers:
    every = {"deliver": 5.0, "schedule": 15.0, "bot_updates": 5.0, "webhook_inbox": 2.0}
    every.update(intervals or {})
    conn = state.conn
    engine = Engine(conn, adapters.delivery, clock=clock)
    loops = [
        Loop("deliver", lambda: _deliver_sending(conn, engine, lease), every["deliver"], True),
        Loop(
            "schedule", lambda: run_due(lease, conn, engine, now=clock()), every["schedule"], True
        ),
    ]
    if adapters.poller is not None:
        loops.append(Loop("bot_updates", adapters.poller.poll_once, every["bot_updates"], False))
    if adapters.worker is not None:
        loops.append(Loop("webhook_inbox", adapters.worker.run_once, every["webhook_inbox"], False))
    return Workers(conn, tuple(loops), clock=clock)
