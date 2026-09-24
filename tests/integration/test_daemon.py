"""The daemon: fail-closed start, one per runtime dir, sockets and clean stop."""

import asyncio
import os
from pathlib import Path

import pytest

from comms.transports.telegram.ipc.framing import (
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)
from comms.transports.telegram.keys.store import provision_missing, set_store_dir
from comms.transports.telegram.runtime.daemon import DaemonConfig, DaemonError, run_daemon
from tests.telegram.fake_client import FakeClient


def _config(tmp_path):
    # Relative runtime dir: AF_UNIX paths cap near 104 bytes (tests chdir into tmp_path).
    return DaemonConfig(
        runtime_dir=Path("run"),
        state_dir=tmp_path / "state",
        key_dir=tmp_path / "keys",
        api_id=1,
        port=_port(),
    )


def _port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _provision(tmp_path):
    # No agent pins: comms spec v0.2 has no consent agent to pair.
    provision_missing(tmp_path / "keys", phases=(2, 3))
    set_store_dir(tmp_path / "keys")


async def _admin(cmd):
    reader, writer = await asyncio.open_unix_connection("run/admin.sock")
    await write_frame(writer, encode_json_frame({"cmd": cmd, "args": {}}))
    reply = decode_json_frame(await read_frame(reader))
    writer.close()
    return reply


async def test_an_unreachable_telegram_does_not_stop_the_daemon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _provision(tmp_path)

    class Down(FakeClient):
        async def connect(self):
            raise ConnectionError("unreachable")

    stop = asyncio.Event()
    daemon = asyncio.create_task(
        run_daemon(
            _config(tmp_path),
            api_hash_reader=lambda: "0" * 32,
            client_factory=lambda *a, **k: Down(),
            stop=stop,
        )
    )
    for _ in range(100):
        if os.path.exists("run/admin.sock") or daemon.done():
            break
        await asyncio.sleep(0.05)
    try:
        assert not daemon.done(), "the daemon died with Telegram"
        status = (await _admin("auth status"))["data"]
        assert status["state"] == "TELEGRAM_UNAVAILABLE" and status["authorized"] is False
    finally:
        stop.set()
        await asyncio.wait_for(daemon, 10)


async def test_production_shape_socket_is_group_0660_and_needs_the_installer(tmp_path, monkeypatch):
    import grp
    import stat

    monkeypatch.chdir(tmp_path)
    _provision(tmp_path)
    group = grp.getgrgid(os.getgid()).gr_name  # a group this test process really is in
    config = DaemonConfig(
        runtime_dir=Path("run"),
        state_dir=tmp_path / "state",
        key_dir=tmp_path / "keys",
        api_id=1,
        port=_port(),
        admin_group=group,
    )
    with pytest.raises(DaemonError, match="installer"):
        await run_daemon(
            config, api_hash_reader=lambda: "0" * 32, client_factory=lambda *a, **k: FakeClient()
        )
    os.mkdir("run", 0o750)  # what the installer provides
    stop = asyncio.Event()
    daemon = asyncio.create_task(
        run_daemon(
            config,
            api_hash_reader=lambda: "0" * 32,
            client_factory=lambda *a, **k: FakeClient(),
            stop=stop,
        )
    )
    for _ in range(100):
        if os.path.exists("run/admin.sock") or daemon.done():
            break
        await asyncio.sleep(0.05)
    try:
        st = os.stat("run/admin.sock")
        assert stat.S_IMODE(st.st_mode) == 0o660 and st.st_gid == os.getgid()
        assert stat.S_IMODE(os.stat("run").st_mode) == 0o750  # the daemon did not touch it
        assert (await _admin("client list"))["ok"] is True  # admitted by group, not by uid
    finally:
        stop.set()
        await asyncio.wait_for(daemon, 10)


@pytest.mark.platform_gated
def test_the_installed_runtime_directory_has_the_production_boundary():
    """Spec §9.4 on this host. Skips honestly until the installer has run."""
    import grp
    import pwd
    import stat

    path = Path("/private/var/run/telegram-mcp")
    try:
        owner = pwd.getpwnam("telegram-mcpd").pw_uid
        admin = grp.getgrnam("telegram-mcp-admin").gr_gid
    except KeyError:
        pytest.skip("service accounts are not installed on this host")
    if not path.is_dir():
        pytest.skip("the runtime directory is not installed on this host")
    st = path.stat()
    assert (st.st_uid, st.st_gid) == (owner, admin)
    assert stat.S_IMODE(st.st_mode) & 0o007 == 0  # nothing for others


async def test_the_daemon_serves_admin_and_stops_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _provision(tmp_path)
    stop = asyncio.Event()
    fake = FakeClient(authorized=False)
    daemon = asyncio.create_task(
        run_daemon(
            _config(tmp_path),
            api_hash_reader=lambda: "0" * 32,
            client_factory=lambda *a, **k: fake,
            stop=stop,
        )
    )
    for _ in range(100):
        if os.path.exists("run/admin.sock"):
            break
        await asyncio.sleep(0.05)
    try:
        assert (await _admin("client list"))["ok"] is True
        assert (await _admin("auth status"))["data"] == {"authorized": False, "revoked": False}
        second = asyncio.create_task(
            run_daemon(
                _config(tmp_path),
                api_hash_reader=lambda: "0" * 32,
                client_factory=lambda *a, **k: FakeClient(),
            )
        )
        with pytest.raises(DaemonError, match="already running"):
            await second
    finally:
        stop.set()
        await asyncio.wait_for(daemon, 10)
    assert fake.connected is False and fake.logged_out is False
    assert not os.path.exists("run/admin.sock")
