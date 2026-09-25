"""Telethon on its own thread and event loop (D39-PRE Task E10a).

The comms user-actor adapters are synchronous and run on the daemon's event-loop thread (one
SQLite connection, one thread). Telethon is asynchronous and bound to the loop it was created
on, so it cannot be driven from that same loop without re-entering it. The daemon therefore
builds and runs its one ``TelethonSession`` on a dedicated thread:

- synchronous callers (the adapters' ``run``) block on ``TelethonThread.run`` until Telethon
  answers or the timeout passes; the future is then cancelled and ``TimeoutError`` raised;
- asynchronous callers (the retained login handlers) use ``LoopBoundSession``, whose coroutine
  methods hop to the Telethon thread and are awaited without blocking the daemon's loop.

A call made from the Telethon thread itself is refused: it would wait on its own loop forever.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from collections.abc import Coroutine
from typing import Any

__all__ = ["LoopBoundSession", "TelethonThread"]


class TelethonThread:
    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def __repr__(self) -> str:
        return "TelethonThread(<redacted>)"

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("the Telethon thread is not running")
        return self._loop

    @property
    def ident(self) -> int | None:
        return None if self._thread is None else self._thread.ident

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        def main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._thread = threading.Thread(target=main, name="telethon", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _submit(self, coroutine: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        if threading.current_thread() is self._thread:
            coroutine.close()
            raise RuntimeError("a Telethon call from the Telethon thread would wait on itself")
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        """Run on the Telethon loop and wait for the result (the adapters' ``Runner``)."""
        future = self._submit(coroutine)
        try:
            return future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("the Telegram call did not finish in time") from None

    async def call(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        """Await a coroutine on the Telethon loop from another event loop."""
        return await asyncio.wrap_future(self._submit(coroutine))

    def stop(self) -> None:
        if self._loop is not None and self._thread is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)


class LoopBoundSession:
    """The session as the daemon's loop sees it: coroutine methods hop to the Telethon thread,
    everything else (state flags, ``readiness()``) is read directly."""

    def __init__(self, session: Any, thread: TelethonThread) -> None:
        self._session, self._thread = session, thread

    def __repr__(self) -> str:
        return "LoopBoundSession(<redacted>)"

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._session, name)
        if not inspect.iscoroutinefunction(attribute):
            return attribute

        async def hop(*args: Any, **kwargs: Any) -> Any:
            return await self._thread.call(attribute(*args, **kwargs))

        return hop
