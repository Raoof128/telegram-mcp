"""On-demand runtime lifecycle: ordered startup, DRAINING shutdown.

``RuntimeContext`` carries the per-start 128-bit ``runtime_id``
(``secrets.token_bytes(16)``, memory-only, fresh per start). ``drain()``
is the drain-phase unit: mark DRAINING, route new calls to the fixed
``INTERNAL_ERROR`` result, give in-flight work ``grace`` seconds (default
5.0), then cancel the remainder. ``startup()`` runs steps 1-17 in order
with injectable seams so tests run headless; ``shutdown()`` drains, then
closes Telegram (disconnect-only, never ``log_out``), SQLite, sockets,
the kernel lock, and the consent UI.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DRAINING_INTERNAL_ERROR",
    "STARTUP_STEPS",
    "RuntimeContext",
    "StartupFailed",
    "drain",
    "run_lifecycle",
    "shutdown",
    "startup",
]

# Fixed result for any new sensitive dispatch arriving during DRAINING.
# Controller decision: bounded INTERNAL_ERROR, never hang or half-execute.
DRAINING_INTERNAL_ERROR = "INTERNAL_ERROR"

# Ordered startup steps 1-17, in the design's normative order (§1).
#
# Two orderings in that list are load-bearing and were wrong here until the
# end-to-end smoke caught them:
#
# * The single-runtime lock is step 2, before any secret is read and before
#   the database is opened. Acquiring it later let a second start load
#   secrets, open SQLite, migrate and garbage-collect cursors before it
#   discovered the first runtime — two processes touching shared state on
#   their way to a fail-closed refusal.
# * READY is advertised before the tunnel client starts, never after. The
#   design says no tunnel polling before READY, and this module's own
#   comment claimed that while the tuple did the opposite.
STARTUP_STEPS: tuple[str, ...] = (
    "mint_runtime_id",  # 1
    "acquire_lock",  # 2
    "load_secrets",  # 3
    "verify_key_permissions",  # 4
    "open_db",  # 5
    "run_migrations",  # 6
    "gc_cursors",  # 7
    "recompute_key_ids",  # 8
    "bind_admin_socket",  # 9
    "bind_consent_socket",  # 10
    "handshake_consent",  # 11
    "connect_telegram",  # 12
    "verify_authority_snapshot",  # 13
    "open_listeners",  # 14
    "verify_ports",  # 15
    "mark_ready",  # 16
    "start_tunnel",  # 17
)

# Seams injectable as callables for headless tests (brief Step 6).
SEAM_STEPS = ("load_secrets", "open_db", "handshake_consent", "open_listeners", "start_tunnel")


class StartupFailed(Exception):
    """A startup step failed. Fixed safe message; aborts before READY."""

    def __init__(self, step: str) -> None:
        self.step = step
        super().__init__(f"startup failed at step {step}")


@dataclass
class RuntimeContext:
    """Per-start runtime state. ``runtime_id`` is 128 bits, memory-only."""

    runtime_id: bytes
    started_at: float
    state: str = "STARTING"
    mode: str | None = None
    lock: Any | None = field(default=None, repr=False)
    executed_steps: list[str] = field(default_factory=list, repr=False)


def _default_drain_result() -> str:
    return DRAINING_INTERNAL_ERROR


async def drain(
    ctx: RuntimeContext,
    *,
    grace: float = 5.0,
    new_call: Callable[[], Any] | None = None,
    inflight: tuple[asyncio.Task[Any], ...] | list[asyncio.Task[Any]] = (),
) -> Any:
    """Run the drain phase: mark DRAINING, bound new calls, reap in-flight.

    Any ``new_call`` invoked during drain routes to the fixed
    INTERNAL_ERROR result. In-flight tasks get ``grace`` seconds, then the
    remainder are cancelled. Returns the new-call result.
    """
    ctx.state = "DRAINING"
    result = (new_call or _default_drain_result)()
    pending = [t for t in inflight if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=grace)
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    return result


def _noop_step(ctx: RuntimeContext) -> None:
    return None


def startup(
    config: Any,
    *,
    mode: str = "local",
    lock_path: str | Path | None = None,
    **seams: Callable[[RuntimeContext], Any],
) -> RuntimeContext:
    """Run ordered startup steps 1-17. Each failure raises fixed ``StartupFailed``."""
    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=time.time(), mode=mode)
    for step in STARTUP_STEPS:
        fn = seams.get(step)
        try:
            if fn is not None:
                fn(ctx)
            elif step == "mint_runtime_id":
                ctx.runtime_id = secrets.token_bytes(16)
            elif step == "acquire_lock" and lock_path is not None:
                from telegram_mcp.runtime.lock import acquire_lock

                ctx.lock = acquire_lock(lock_path, runtime_id=ctx.runtime_id, mode=mode)
            else:
                _noop_step(ctx)
        except StartupFailed:
            raise
        except Exception as exc:
            raise StartupFailed(step) from exc
        ctx.executed_steps.append(step)
    ctx.state = "READY"
    return ctx


async def shutdown(
    ctx: RuntimeContext,
    *,
    telegram: Any | None = None,
    db: Any | None = None,
    sockets: tuple[str | Path, ...] | list[str | Path] = (),
    consent_ui: Any | None = None,
    grace: float = 5.0,
) -> None:
    """Drain, then release every resource in order.

    Telegram is disconnect-only: a ``log_out`` symbol on the adapter is a
    hard error (shutdown must never log the user out).
    """
    await drain(ctx, grace=grace)
    if telegram is not None:
        if hasattr(telegram, "log_out"):
            raise AssertionError("shutdown must never call Telegram log_out")
        result = telegram.disconnect()
        if isinstance(result, Awaitable):
            await result
    if db is not None:
        result = db.close()
        if isinstance(result, Awaitable):
            await result
    for sock in sockets:
        try:
            Path(sock).unlink()
        except FileNotFoundError:
            pass
    lock, ctx.lock = ctx.lock, None
    if lock is not None:
        lock.release()
    if consent_ui is not None:
        result = consent_ui.stop()
        if isinstance(result, Awaitable):
            await result
    ctx.state = "OFF"


def run_lifecycle(
    config: Any,
    *,
    mode: str = "local",
    stopped: threading.Event | None = None,
    lock_path: str | Path | None = None,
    **seams: Callable[[RuntimeContext], Any],
) -> None:
    """Run startup steps 1-17, block on ``stopped``, then DRAINING shutdown.

    ``stopped`` is the stop-signal source (set by the admin-socket stop
    path in production); a pre-set event runs start-then-stop headlessly.
    """
    ctx = startup(config, mode=mode, lock_path=lock_path, **seams)
    gate = stopped if stopped is not None else threading.Event()
    gate.wait()
    asyncio.run(shutdown(ctx, grace=5.0))
