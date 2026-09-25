"""D39-PRE Task E10a: the comms daemon runs Telethon on its own thread, with updates on.

In process, over a fake Telethon client: the session is built with ``receive_updates``, connects
on the Telethon thread (not the daemon's loop), and the retained login handlers reach it through
the loop-bound session over the real admin socket.
"""

import asyncio
import json
import socket
import threading
from pathlib import Path

from comms.cli import _admin_request
from comms.runtime.paths import CommsPaths
from comms.runtime.provision import provision
from comms.transports.telegram.runtime.daemon import DaemonConfig, run_daemon
from tests.runtime.test_provision import NOW
from tests.telegram.fake_client import FakeClient


class ThreadRecordingClient(FakeClient):
    async def connect(self):
        self.connect_thread = threading.get_ident()
        await super().connect()


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_comms_daemon_runs_telethon_on_its_own_thread_with_updates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run").mkdir(mode=0o700)
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=Path("run"))
    paths.settings.write_text(json.dumps({"local_port": _port()}), encoding="utf-8")
    paths.settings.chmod(0o600)
    fake = ThreadRecordingClient()
    captured = {}

    def factory(path, api_id, api_hash, **kwargs):
        captured.update(kwargs)
        return fake

    config = DaemonConfig(runtime_dir=Path("run"), state_dir=paths.state_dir,
                          key_dir=paths.legacy_keys, api_id=1, comms=True)  # fmt: skip

    async def scenario():
        stop = asyncio.Event()
        daemon = asyncio.create_task(
            run_daemon(config, api_hash_reader=lambda: "0" * 32, client_factory=factory, stop=stop)
        )
        for _ in range(400):
            if Path("run/admin.sock").exists() or daemon.done():
                break
            await asyncio.sleep(0.02)
        try:
            status = await asyncio.to_thread(
                _admin_request, "run", {"cmd": "auth status", "args": {}}
            )
            assert status["ok"] is True, status
        finally:
            stop.set()
            await asyncio.wait_for(daemon, 20)

    asyncio.run(scenario())
    assert captured["receive_updates"] is True
    assert len(fake.handlers) == 1  # the one raw-update handler
    assert fake.connect_thread != threading.get_ident()  # connected on the Telethon thread
