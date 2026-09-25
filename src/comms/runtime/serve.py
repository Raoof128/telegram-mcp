"""The comms side of the daemon (D39-PRE Task E6).

``CommsServer.start`` runs after the daemon holds its lock and before the admin socket opens,
in this order, each fail-closed:

1. ``load_settings`` (``comms/comms.json``);
2. ``open_comms_state`` (key, migration, integrity, required keys, anchor);
3. writes are held until the cutover completes: a state whose cutover is not ``COMPLETE``
   starts with the integrity latch set (``CUTOVER_PENDING``). An audited write before the
   genesis would leave the comms chain non-empty, and the cutover requires it empty. Reads
   work; ``comms cutover run`` releases the hold;
4. ``startup_recovery`` (interrupted mutations, campaigns mid-delivery);
5. ``assemble_runtime`` (the one composition root; the adapter factory is the only seam);
6. the listeners (local always; remote and webhook when configured), each bound to 127.0.0.1;
7. the workers.

uvicorn captures SIGTERM/SIGINT while it serves, then restores the daemon's handlers and
re-raises the signal, so one signal stops the listeners and the daemon alike.

``stop`` runs in reverse. A worker's fatal failure sets ``failed`` and the daemon's stop
event, so the daemon exits nonzero after a clean shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from comms.core.audit.cutover import current_phase
from comms.core.audit.integrity import latch_degraded
from comms.core.delivery.engine import ExecutorLease
from comms.runtime.adapters import UPDATE_OWNER
from comms.runtime.assemble import AdaptersFactory, assemble_runtime, production_adapters
from comms.runtime.operator import LegacySide
from comms.runtime.operator.cutover import CUTOVER_PENDING
from comms.runtime.paths import CommsPaths
from comms.runtime.settings import SettingsError, load_settings
from comms.runtime.state import CommsState, StateRefused, open_comms_state
from comms.runtime.workers import DaemonStop, Workers, build_workers, startup_recovery

__all__ = ["CommsServer", "CommsStartRefused", "legacy_side"]

_logger = logging.getLogger("comms.serve")

Handler = Callable[[dict[str, Any]], Any]


class CommsStartRefused(Exception):
    """The comms side refused to start; the message is fixed and names the fix."""


def _now() -> datetime:
    return datetime.now(UTC)


def legacy_side(legacy_conn: Any, paths: CommsPaths) -> LegacySide:
    """The retained legacy chain, from the daemon's ``meta.db`` and the legacy key store (bound
    by the daemon before this runs)."""
    from comms.transports.telegram.disclosure.keys import checkpoint_public_for
    from comms.transports.telegram.keys.store import load_key
    from comms.transports.telegram.runtime.cutover_barrier import (
        CutoverGate,
        TelegramLegacyPort,
        legacy_verifier,
    )
    from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
    from comms.transports.telegram.storage.settings import get_setting

    chain_key = load_key("audit-chain-key")

    def port() -> TelegramLegacyPort:
        return TelegramLegacyPort(
            legacy_conn,
            chain_key,
            load_key("audit-checkpoint-key"),
            paths.legacy_anchor,
            CutoverGate(),  # v0.3 has no legacy ingress: nothing is ever in flight
            paths.legacy_keys,
        )

    def retention() -> TelegramLegacyRetention:
        return TelegramLegacyRetention(legacy_conn, chain_key, checkpoint_public_for(legacy_conn))

    def retention_days() -> dict[str, int]:
        return {
            "exposure_ledger_days": int(get_setting(legacy_conn, "retention.exposure_ledger_days")),
            "receipt_days": int(get_setting(legacy_conn, "retention.disclosure_receipt_days")),
            "message_ref_days": int(get_setting(legacy_conn, "retention.message_ref_days")),
            "audit_events_days": int(get_setting(legacy_conn, "retention.audit_events_days")),
        }

    return LegacySide(
        conn=legacy_conn,
        port=port,
        verify=legacy_verifier(chain_key, checkpoint_public_for(legacy_conn)),
        retention=retention,
        retention_days=retention_days,
    )


@dataclass
class CommsServer:
    state_dir: Path
    lock: Any  # the daemon's runtime LockHandle: the campaign engine's executor lease
    stop_event: asyncio.Event
    legacy_conn: Any = None  # the daemon's meta.db: verify --all and the cutover
    telegram_session: Any = None  # E10a: the one TelethonSession, on its own thread
    telegram_run: Callable[[Any], Any] | None = None  # E10a: TelethonThread.run
    adapters_factory: AdaptersFactory | None = None
    admin_handlers: Mapping[str, Handler] = field(default_factory=dict)
    control_handlers: Mapping[str, Handler] = field(default_factory=dict)
    failed: bool = False
    _state: CommsState | None = None
    _servers: list[uvicorn.Server] = field(default_factory=list)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    _assemble: Callable[[], Any] | None = None
    _dispatcher: Any = None
    _lease: Any = None
    _workers_stop: asyncio.Event | None = None
    _webhook_served: bool = False
    _loop: asyncio.AbstractEventLoop | None = None

    def __repr__(self) -> str:
        return "CommsServer(<redacted>)"

    @property
    def writer(self) -> Any:
        """The comms audit writer (the legacy session revoke records through it)."""
        return None if self._state is None else self._state.writer

    async def start(self) -> None:
        paths = CommsPaths(self.state_dir)
        try:
            settings = load_settings(paths.settings)
        except SettingsError as refused:
            raise CommsStartRefused(str(refused)) from None
        try:
            state = open_comms_state(paths, clock=_now)
        except StateRefused as refused:
            raise CommsStartRefused(str(refused)) from None
        self._state = state
        if current_phase(state.conn)[0] != "COMPLETE":
            latch_degraded(state.conn, reason=CUTOVER_PENDING, now=_now())
        lease = ExecutorLease(self.lock)
        startup_recovery(state, lease, now=_now())
        factory = self.adapters_factory or production_adapters(
            clock=_now,
            monotonic=time.monotonic,
            archive=None,
            telegram_session=self.telegram_session,
            run=self.telegram_run,
        )
        legacy = None if self.legacy_conn is None else legacy_side(self.legacy_conn, paths)

        def assemble() -> Any:
            return assemble_runtime(
                state,
                settings,
                adapters_factory=factory,
                clock=_now,
                monotonic=time.monotonic,
                legacy=legacy,
                reload=self.reload,
            )

        self._assemble, self._lease = assemble, lease
        self._loop = asyncio.get_running_loop()
        assembled = assemble()
        self._bind_updates(assembled.adapters)
        runtime = assembled.runtime
        self._dispatcher = runtime.dispatcher
        self.admin_handlers = runtime.admin_handlers
        self.control_handlers = runtime.control_handlers
        apps = [(runtime.listeners.local, settings.local_port)]
        if runtime.listeners.remote is not None and settings.remote is not None:
            apps.append((runtime.listeners.remote, settings.remote.port))
        if runtime.listeners.webhook is not None and settings.webhook_port is not None:
            apps.append((runtime.listeners.webhook, settings.webhook_port))
            self._webhook_served = True
        for app, port in apps:
            server = uvicorn.Server(
                uvicorn.Config(app, host=settings.host, port=port, log_level="warning")
            )
            self._servers.append(server)
            self._tasks.append(asyncio.create_task(server.serve()))
        while not all(s.started for s in self._servers):
            if any(t.done() for t in self._tasks):
                raise CommsStartRefused("a comms listener could not bind its port")
            await asyncio.sleep(0.01)
        self._start_workers(assembled.adapters)

    def _start_workers(self, adapters: Any) -> None:
        assert self._state is not None
        if self._workers_stop is not None:
            self._workers_stop.set()  # the previous generation finishes its step and returns
        self._workers_stop = asyncio.Event()
        operator = self.admin_handlers.get("operator")
        maintenance = None
        if self.legacy_conn is not None and operator is not None:

            def maintenance() -> Any:  # the same handler the owner's `retention run` reaches
                return operator({"command": ["retention", "run"]})

        workers: Workers = build_workers(
            self._state, adapters, self._lease, clock=_now, maintenance=maintenance
        )
        self._tasks.append(asyncio.create_task(self._supervise(workers, self._workers_stop)))

    async def _supervise(self, workers: Workers, generation: asyncio.Event) -> None:
        async def daemon_stopped() -> None:
            await self.stop_event.wait()
            generation.set()

        watcher = asyncio.create_task(daemon_stopped())
        try:
            await workers.run(generation)
        except DaemonStop:
            self.failed = True
            self.stop_event.set()
        finally:
            watcher.cancel()

    def _bind_updates(self, adapters: Any) -> None:
        """Point the session's update stream at this generation's consumer (E10a). Telethon
        calls the sink on its own thread; the batch is posted to this loop, which owns the one
        SQLite connection."""
        consumer, session, loop = adapters.updates, self.telegram_session, self._loop
        if consumer is None or session is None or loop is None:
            return

        def consume(updates: Any) -> None:
            try:
                consumer.consume(updates)
            except Exception:  # noqa: BLE001 -- a write that failed is a safety failure
                _logger.error("update_consume_failed")
                if self._state is not None:
                    latch_degraded(self._state.conn, reason="UPDATE_CONSUME_FAILURE", now=_now())

        session.set_update_sink(
            UPDATE_OWNER, lambda updates: loop.call_soon_threadsafe(consume, updates)
        )

    def reload(self) -> dict[str, Any]:
        """Rebuild the adapters after a credential change (D39-PRE E8a).

        The services are re-assembled over the new adapters and the one dispatcher is rebound
        to them, so every listener and the admin socket use the new credential at once; the
        workers restart on the new adapters. A webhook listener that was not served at start
        needs a restart to bind its port, and the reply says so.
        """
        if self._assemble is None or self._dispatcher is None:
            return {"reloaded": False}
        assembled = self._assemble()
        self._bind_updates(assembled.adapters)
        self._dispatcher.rebind(assembled.runtime.dispatcher.registry)
        self._start_workers(assembled.adapters)
        webhook_waits = assembled.runtime.listeners.webhook is not None and not self._webhook_served
        return {"reloaded": True, "restart_needed": webhook_waits}

    async def stop(self) -> None:
        self.stop_event.set()
        for server in self._servers:
            server.should_exit = True
        for task in self._tasks:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=10)
        if self._state is not None:
            self._state.conn.close()
            self._state = None
