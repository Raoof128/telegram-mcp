"""D39-PRE Task E8a: a credential change rebuilds the adapters inside the running daemon."""

import asyncio
import json
import socket
from pathlib import Path

from comms.runtime.paths import CommsPaths
from comms.runtime.provision import provision
from comms.runtime.selftest import selftest_adapters
from comms.runtime.serve import CommsServer
from tests.runtime.test_provision import NOW


class Held:
    def held(self):
        return True


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_reload_rebinds_the_dispatcher_and_restarts_the_workers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run").mkdir(mode=0o700)
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=Path("run"))
    paths.settings.write_text(json.dumps({"local_port": _port()}), encoding="utf-8")
    paths.settings.chmod(0o600)
    built = []

    def factory(state, settings):
        built.append(1)
        return selftest_adapters(state, settings)

    async def scenario():
        server = CommsServer(state_dir=paths.state_dir, lock=Held(), stop_event=asyncio.Event(),
                             adapters_factory=factory)  # fmt: skip
        await server.start()
        try:
            before = server._dispatcher.registry
            workers_before = server._workers_stop
            reply = server.admin_handlers["operator"]  # the reload is reached through the context
            assert callable(reply)
            result = server.reload()
            await asyncio.sleep(0.05)
            assert result == {"reloaded": True, "restart_needed": False}
            assert server._dispatcher.registry is not before  # every listener sees new services
            assert workers_before.is_set() and not server._workers_stop.is_set()
            assert len(built) == 2
        finally:
            await server.stop()

    asyncio.run(scenario())
