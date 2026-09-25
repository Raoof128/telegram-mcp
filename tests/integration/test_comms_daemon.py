"""D39-PRE Task E6: the real daemon, in its own process, fake providers via ``selftest-daemon``.

Everything goes through the installed ``comms`` binary, the admin Unix socket, and the real
stdio proxy under a real MCP client. Nothing here imports the composition.
"""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from comms.mcp.catalog import TOOL_CATALOG

COMMS = str(Path(sys.executable).parent / "comms")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Daemon:
    def __init__(self, root: Path):
        self.state, self.run = Path("state"), Path("run")
        self.port = _free_port()
        self.env = {**os.environ, "TELEGRAM_MCP_RUNTIME_DIR": str(self.run)}
        self.proc = None

    def comms(self, *argv, check=True):
        done = subprocess.run([COMMS, *argv], env=self.env, capture_output=True, text=True,
                              check=False, timeout=60)  # fmt: skip
        if check:
            assert done.returncode == 0, done.stderr
        return done

    def provision(self):
        self.comms(
            "keys", "provision", "--state-dir", str(self.state), "--runtime-dir", str(self.run)
        )
        settings = self.state / "comms" / "comms.json"
        settings.write_text(json.dumps({"local_port": self.port}), encoding="utf-8")
        settings.chmod(0o600)

    def start(self, *, wait=True):
        self.proc = subprocess.Popen(
            [COMMS, "selftest-daemon", "--state-dir", str(self.state), "--runtime-dir", str(self.run)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )  # fmt: skip
        if wait:
            for _ in range(400):
                if self.proc.poll() is not None:
                    break
                if (self.run / "admin.sock").exists() and self._listening():
                    return
                time.sleep(0.05)
            raise AssertionError(f"daemon did not start: {self.proc.communicate(timeout=5)[1]}")

    def _listening(self):
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        return self.proc.wait(timeout=20)

    async def mcp(self, seed, action):
        params = StdioServerParameters(
            command=COMMS,
            args=["mcp", "--stdio", "--client-seed", str(seed), "--daemon",
                  f"http://127.0.0.1:{self.port}", "--runtime-dir", str(self.run)],
            env=self.env,
        )  # fmt: skip
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # AF_UNIX path limit
    Path("run").mkdir(mode=0o700)
    d = Daemon(tmp_path)
    d.provision()
    d.start()
    yield d
    if d.proc.poll() is None:
        d.stop()


def test_a_client_lists_reads_and_is_held_before_the_cutover_then_reconnects(daemon):
    added = json.loads(daemon.comms("client", "add", "--name", "t", "--helper-path", "seed").stdout)
    assert added["client"].startswith("cli_")

    async def session_one(session):
        listed = await session.list_tools()
        read = await session.call_tool("comms_location_list", {})
        write = await session.call_tool(
            "comms_location_create", {"name": "Parramatta", "request_id": "req_" + "a" * 26}
        )
        return [t.name for t in listed.tools], read, write

    names, read, write = asyncio.run(daemon.mcp("seed", session_one))
    assert names == [s.name for s in TOOL_CATALOG]
    assert not read.is_error
    # PROVISIONED: no genesis yet, so writes are held (an audited write now would make the
    # cutover impossible); reads work
    assert (
        write.is_error and write.structured_content["error"]["code"] == "AUDIT_INTEGRITY_DEGRADED"
    )

    assert daemon.stop() == 0  # SIGTERM is a clean shutdown
    assert not (daemon.run / "admin.sock").exists()
    daemon.start()  # restart; the same client reconnects

    async def session_two(session):
        return [t.name for t in (await session.list_tools()).tools]

    assert asyncio.run(daemon.mcp("seed", session_two)) == [s.name for s in TOOL_CATALOG]


def test_a_second_daemon_on_the_same_runtime_dir_is_refused(daemon):
    second = subprocess.run(
        [COMMS, "selftest-daemon", "--state-dir", str(daemon.state), "--runtime-dir", str(daemon.run)],
        env=daemon.env, capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    assert second.returncode != 0 and "already running" in second.stderr


def test_a_key_that_does_not_open_comms_db_refuses_the_start(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run").mkdir(mode=0o700)
    d = Daemon(tmp_path)
    d.provision()
    key = next((d.state / "comms" / "secrets" / "comms-db-key").iterdir())
    key.write_bytes(os.urandom(32))  # same mode, wrong material
    d.start(wait=False)
    assert d.proc.wait(timeout=30) != 0
    assert "does not open comms.db" in d.proc.stderr.read()
    assert not (d.run / "admin.sock").exists()


def test_the_cutover_releases_writes_then_a_write_replays_and_verify_all_is_clean(daemon):
    daemon.comms("client", "add", "--name", "t", "--helper-path", "seed")
    status = json.loads(daemon.comms("cutover", "status").stdout)
    assert status["phase"] == "NONE" and status["writes_held"] is True
    done = json.loads(daemon.comms("cutover", "run").stdout)
    assert done == {"phase": "COMPLETE", "writes_released": True}

    request = "req_" + "b" * 26

    async def writes(session):
        args = {"name": "Parramatta", "request_id": request}
        first = await session.call_tool("comms_location_create", args)
        again = await session.call_tool("comms_location_create", args)
        listed = await session.call_tool("comms_location_list", {})
        return first, again, listed

    first, again, listed = asyncio.run(daemon.mcp("seed", writes))
    assert not first.is_error and first.structured_content["location"].startswith("loc_")
    assert again.structured_content["location"] == first.structured_content["location"]
    assert again.structured_content["replayed"] is True
    names = [item["name"] for item in listed.structured_content["items"]]
    assert names.count("Parramatta") == 1  # the replay made no second effect

    report = json.loads(daemon.comms("audit", "verify", "--all").stdout)
    assert report == {"ok": True, "legacy": "ok", "lineage": "ok", "comms": "ok", "problems": []}

    # the daemon's maintenance loop resumes once the hold is released and runs retention
    for _ in range(100):
        doctor = json.loads(daemon.comms("doctor", "--state-dir", str(daemon.state)).stdout)
        if doctor["ok"]:
            break
        time.sleep(0.1)
    assert doctor["bootstrap"] == "READY"
    assert {f["code"] for f in doctor["findings"]} == {"CREDENTIAL_NOT_CONFIGURED"}
