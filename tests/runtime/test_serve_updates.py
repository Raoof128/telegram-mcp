"""D39-PRE Task E10a: the update stream reaches the consumer on the daemon's own loop.

Telethon delivers updates on its own thread; the consumer writes through the daemon's one SQLite
connection, so each batch is posted to the daemon's loop. After a credential reload the sink
points at the new consumer.
"""

import asyncio
import json
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path

from comms.runtime.adapters import UPDATE_OWNER
from comms.runtime.paths import CommsPaths
from comms.runtime.provision import provision
from comms.runtime.selftest import selftest_adapters
from comms.runtime.serve import CommsServer
from comms.transports.telegram.telegram.updates_view import NeutralUpdate
from comms.transports.telegram.user.updates import UserUpdateConsumer
from tests.runtime.test_provision import NOW


class Held:
    def held(self):
        return True


class FakeSession:
    def __init__(self):
        self.owner, self.sink = None, None

    def claim_updates(self, owner):
        assert self.owner in (None, owner)
        self.owner = owner

    def set_update_sink(self, owner, sink):
        assert owner == self.owner
        self.sink = sink


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_updates_from_the_telethon_thread_are_written_on_the_daemon_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run").mkdir(mode=0o700)
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=Path("run"))
    paths.settings.write_text(json.dumps({"local_port": _port()}), encoding="utf-8")
    paths.settings.chmod(0o600)
    session = FakeSession()
    consumers = []

    def factory(state, settings):
        adapters = selftest_adapters(state, settings)
        session.claim_updates(UPDATE_OWNER)
        adapters.updates = UserUpdateConsumer(state.conn, clock=lambda: datetime.now(UTC))
        consumers.append(adapters.updates)
        return adapters

    async def scenario():
        server = CommsServer(state_dir=paths.state_dir, lock=Held(), stop_event=asyncio.Event(),
                             adapters_factory=factory, telegram_session=session)  # fmt: skip
        await server.start()
        try:
            update = NeutralUpdate("message", chat="-1001234", message_id=5, payload={"text": "hi"})
            sender = threading.Thread(target=session.sink, args=([update],))  # the Telethon thread
            sender.start()
            sender.join()
            await asyncio.sleep(0.05)  # the posted batch runs on this loop
            rows = server._state.conn.execute("SELECT event_ref FROM user_updates").fetchall()
            assert rows == [("-1001234:5",)]
            first_sink = session.sink
            server.reload()
            assert session.sink is not first_sink and len(consumers) == 2
        finally:
            await server.stop()

    asyncio.run(scenario())
