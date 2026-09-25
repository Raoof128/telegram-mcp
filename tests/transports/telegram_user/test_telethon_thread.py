"""D39-PRE Task E10a: Telethon on its own thread and loop.

The user-actor adapters are synchronous and run on the daemon's event-loop thread; Telethon is
asynchronous and bound to the loop it was created on. The daemon therefore runs the one Telethon
session on a dedicated thread: synchronous callers block on ``run``; the async login handlers
reach it through ``LoopBoundSession``, whose coroutine methods hop to the Telethon thread.
"""

import asyncio
import threading

import pytest

from comms.transports.telegram.runtime.telethon_thread import LoopBoundSession, TelethonThread


class FakeSession:
    def __init__(self):
        self.threads = []
        self.revoked = False

    async def send_code(self, phone, deadline=None):
        self.threads.append(threading.get_ident())
        await asyncio.sleep(0)
        return f"sent {phone}"

    async def slow(self):
        await asyncio.sleep(10)

    def readiness(self):
        return "READY"


@pytest.fixture
def thread():
    t = TelethonThread(timeout=2.0)
    t.start()
    yield t
    t.stop()


def test_run_executes_on_the_telethon_thread_and_returns_the_result(thread):
    session = FakeSession()
    assert thread.run(session.send_code("+61400000001")) == "sent +61400000001"
    assert session.threads == [thread.ident] and thread.ident != threading.get_ident()


def test_run_from_the_daemon_loop_does_not_deadlock(thread):
    session = FakeSession()

    async def daemon_handler():  # a sync adapter call inside the daemon's running loop
        return thread.run(session.send_code("+1"))

    assert asyncio.run(daemon_handler()) == "sent +1"


def test_the_bound_session_hops_coroutines_and_passes_plain_attributes(thread):
    session = FakeSession()
    bound = LoopBoundSession(session, thread)

    async def login_handler():
        return await bound.send_code("+61400000002")

    assert asyncio.run(login_handler()) == "sent +61400000002"
    assert session.threads == [thread.ident]
    assert bound.readiness() == "READY" and bound.revoked is False
    assert "redacted" in repr(bound)


def test_a_call_that_outlives_the_timeout_is_cancelled_and_raises(thread):
    with pytest.raises(TimeoutError):
        thread.run(FakeSession().slow())


def test_run_on_the_telethon_thread_itself_is_refused(thread):
    async def reentrant():
        return thread.run(FakeSession().send_code("+1"))

    with pytest.raises(RuntimeError, match="Telethon thread"):
        thread.run(reentrant())


def test_stop_joins_the_thread():
    t = TelethonThread()
    t.start()
    t.stop()
    assert not t.alive
