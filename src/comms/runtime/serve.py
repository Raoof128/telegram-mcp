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
from comms.runtime.assemble import AdaptersFactory, assemble_runtime, production_adapters
from comms.runtime.paths import CommsPaths
from comms.runtime.settings import SettingsError, load_settings
from comms.runtime.state import CommsState, StateRefused, open_comms_state
from comms.runtime.workers import DaemonStop, Workers, build_workers, startup_recovery

__all__ = ["CUTOVER_PENDING", "CommsServer", "CommsStartRefused"]

CUTOVER_PENDING = "CUTOVER_PENDING"
Handler = Callable[[dict[str, Any]], Any]


class CommsStartRefused(Exception):
    """The comms side refused to start; the message is fixed and names the fix."""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CommsServer:
    state_dir: Path
    lock: Any  # the daemon's runtime LockHandle: the campaign engine's executor lease
    stop_event: asyncio.Event
    adapters_factory: AdaptersFactory | None = None
    admin_handlers: Mapping[str, Handler] = field(default_factory=dict)
    control_handlers: Mapping[str, Handler] = field(default_factory=dict)
    failed: bool = False
    _state: CommsState | None = None
    _servers: list[uvicorn.Server] = field(default_factory=list)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    def __repr__(self) -> str:
        return "CommsServer(<redacted>)"

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
            clock=_now, monotonic=time.monotonic, archive=None
        )
        assembled = assemble_runtime(
            state, settings, adapters_factory=factory, clock=_now, monotonic=time.monotonic
        )
        runtime = assembled.runtime
        self.admin_handlers = runtime.admin_handlers
        self.control_handlers = runtime.control_handlers
        apps = [(runtime.listeners.local, settings.local_port)]
        if runtime.listeners.remote is not None and settings.remote is not None:
            apps.append((runtime.listeners.remote, settings.remote.port))
        if runtime.listeners.webhook is not None and settings.webhook_port is not None:
            apps.append((runtime.listeners.webhook, settings.webhook_port))
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
        workers: Workers = build_workers(state, assembled.adapters, lease, clock=_now)
        self._tasks.append(asyncio.create_task(self._supervise(workers)))

    async def _supervise(self, workers: Workers) -> None:
        try:
            await workers.run(self.stop_event)
        except DaemonStop:
            self.failed = True
            self.stop_event.set()

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
