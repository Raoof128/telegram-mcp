"""Deadlines, per-call work budgets and fair admission (spec §28, §36)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

__all__ = ["Deadline", "DeadlineExceeded", "FairScheduler", "WorkBudget", "WorkBudgetExceeded"]


class DeadlineExceeded(Exception):
    """The request's deadline has passed: DEADLINE_EXCEEDED."""


class WorkBudgetExceeded(Exception):
    """More Telegram requests than the call may make: WORK_BUDGET_EXCEEDED."""


class Deadline:
    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        left = self._end - self._clock()
        if left <= 0:
            raise DeadlineExceeded
        return left


class WorkBudget:
    def __init__(self, max_rpcs: int = 20) -> None:
        self._max = max_rpcs
        self.used = 0

    def spend(self) -> None:
        if self.used >= self._max:
            raise WorkBudgetExceeded
        self.used += 1


class FairScheduler:
    """At most ``per_client`` per client and ``total`` overall; FIFO waiters.

    A waiter whose client is at its own cap is skipped, not blocking the
    queue, so one busy client cannot starve another (spec §28).
    """

    def __init__(self, total: int = 4, per_client: int = 2) -> None:
        self._total = total
        self._per_client = per_client
        self._active: dict[str, int] = {}
        self._running = 0
        self._waiters: deque[tuple[str, asyncio.Future[None]]] = deque()

    def _admissible(self, client: str) -> bool:
        return self._running < self._total and self._active.get(client, 0) < self._per_client

    def _wake(self) -> None:
        for entry in list(self._waiters):
            client, future = entry
            if future.done():
                self._waiters.remove(entry)
                continue
            if self._admissible(client):
                self._waiters.remove(entry)
                self._take(client)
                future.set_result(None)

    def _take(self, client: str) -> None:
        self._running += 1
        self._active[client] = self._active.get(client, 0) + 1

    @asynccontextmanager
    async def slot(self, client: str) -> AsyncIterator[None]:
        if self._admissible(client) and not self._waiters:
            self._take(client)
        else:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append((client, future))
            self._wake()
            try:
                await future
            except asyncio.CancelledError:
                if future.done() and not future.cancelled():
                    self._release(client)
                raise
        try:
            yield
        finally:
            self._release(client)

    def _release(self, client: str) -> None:
        self._running -= 1
        self._active[client] -= 1
        self._wake()
