"""comms v0.3 Task D29: the unprivileged stdio proxy, minting per-request cml1 leases (A34, A33)."""

import asyncio
import json
import os
import socket
from datetime import UTC, datetime

import pytest
import uvicorn

from comms.core.auth import clients, lease_format
from comms.core.keys.slots import KeySlotStore
from comms.core.security import bump_security_epoch, security_epoch
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import Dispatcher
from comms.mcp.http import build_http_app, lease_authenticator
from comms.mcp.stdio_proxy import EPOCH_TTL_S, Proxy, ProxyError, http_post
from comms.services.registry import ServiceRegistry
from tests.core import schema_fixtures as fx
from tests.security.import_closure import closure

PRIVILEGED = (
    "comms.core.keys",
    "comms.core.storage",
    "comms.transports",
    "comms.runtime",
    "comms.services",
    "telethon",
    "sqlcipher3",
    "whatsvault",
)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def daemon(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    (tmp_path / "helper").mkdir(mode=0o700)
    seed_path = tmp_path / "helper" / "seed"
    cli = clients.add_client(
        conn, store, "claude-code", now=datetime.now(UTC), helper_path=seed_path
    )
    seen = []
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(
            spec.service,
            lambda client, arguments, name=spec.service: (
                seen.append((name, client.client_ref)) or {"capabilities": []}
            ),
        )
    port = _free_port()
    app = build_http_app(
        Dispatcher(services),
        lease_authenticator(conn, store, lambda: datetime.now(UTC)),
        host="127.0.0.1",
        port=port,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield {"conn": conn, "port": port, "seed_path": seed_path, "cli": cli, "seen": seen}
    server.should_exit = True
    await task


def _proxy(daemon, *, clock=None, post=None):
    epoch = security_epoch(daemon["conn"])  # read once, on the loop's thread
    return Proxy(
        daemon["seed_path"],
        hello=lambda: epoch,
        post=post or http_post(f"http://127.0.0.1:{daemon['port']}"),
        clock=clock or Clock(),
    )


def test_proxy_imports_nothing_privileged():
    reached = closure("comms.mcp.stdio_proxy")
    assert not [m for m in reached if m.startswith(PRIVILEGED)], reached
    assert "comms.core.auth.lease_format" in reached


async def test_proxy_forwards_tools_list_and_calls(daemon):
    proxy = _proxy(daemon)
    listed = await asyncio.to_thread(proxy.list_tools)
    assert [t["name"] for t in listed["tools"]] == [s.name for s in TOOL_CATALOG]
    called = await asyncio.to_thread(proxy.call_tool, "comms_capability_list", {})
    assert called["isError"] is False and called["structuredContent"]["capabilities"] == []
    assert daemon["seen"] == [("capability.list", daemon["cli"])]


async def test_every_forwarded_request_carries_a_fresh_lease(daemon):
    tokens = []
    real = http_post(f"http://127.0.0.1:{daemon['port']}")

    def post(token, method, params):
        tokens.append(token)
        return real(token, method, params)

    proxy = _proxy(daemon, post=post)
    await asyncio.to_thread(proxy.list_tools)
    await asyncio.to_thread(proxy.call_tool, "comms_capability_list", {})
    payloads = [json.loads(lease_format.decode(t.split(".")[1])) for t in tokens]
    assert len(tokens) == 2 and payloads[0]["nonce"] != payloads[1]["nonce"]
    assert all(0 < p["exp"] - p["iat"] <= 60 for p in payloads)


def test_epoch_bump_picked_up_within_5s_without_restart(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    (tmp_path / "h").mkdir(mode=0o700)
    clients.add_client(conn, store, "c", now=datetime.now(UTC), helper_path=tmp_path / "h" / "s")
    epochs, asked, clock = [], [], Clock()

    def hello():
        asked.append(clock.t)
        return security_epoch(conn)

    def post(token, method, params):
        epochs.append(json.loads(lease_format.decode(token.split(".")[1]))["sec"])
        return {"tools": []}

    proxy = Proxy(tmp_path / "h" / "s", hello=hello, post=post, clock=clock)
    proxy.list_tools()
    bump_security_epoch(conn)
    clock.t += EPOCH_TTL_S - 0.5
    proxy.list_tools()  # still cached: the old epoch, no new hello
    clock.t += 0.6
    proxy.list_tools()  # past 5 s: asks again and mints under the new epoch
    assert epochs == [1, 1, 2] and len(asked) == 2 and EPOCH_TTL_S <= 5.0


def test_seed_file_permissions_enforced_and_never_from_argv_or_env(tmp_path, monkeypatch):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    (tmp_path / "h").mkdir(mode=0o700)
    path = tmp_path / "h" / "s"
    clients.add_client(conn, store, "c", now=datetime.now(UTC), helper_path=path)
    os.chmod(path, 0o640)
    with pytest.raises(ProxyError):
        Proxy(path, hello=lambda: 1, post=lambda *a: {}, clock=Clock())
    os.chmod(path, 0o600)
    link = tmp_path / "h" / "link"
    link.symlink_to(path)
    with pytest.raises(ProxyError):
        Proxy(link, hello=lambda: 1, post=lambda *a: {}, clock=Clock())
    monkeypatch.setenv("COMMS_CLIENT_SEED", lease_format.encode(os.urandom(32)))
    with pytest.raises(ProxyError):  # an env seed is never read, and a missing path is refused
        Proxy(tmp_path / "h" / "missing", hello=lambda: 1, post=lambda *a: {}, clock=Clock())
    from comms.mcp.stdio_proxy import parse_args

    assert parse_args(["--client-seed", str(path)]).client_seed == path
    for bad in (["--seed", "abc"], ["--client-seed"], []):
        with pytest.raises(SystemExit):
            parse_args(bad)


async def test_killing_the_proxy_leaves_the_daemon_session_intact(daemon):
    first = _proxy(daemon)
    await asyncio.to_thread(first.list_tools)
    del first  # a killed proxy holds nothing the daemon needs
    second = _proxy(daemon)
    called = await asyncio.to_thread(second.call_tool, "comms_capability_list", {})
    assert called["isError"] is False
    assert daemon["seen"] == [("capability.list", daemon["cli"])]


def test_the_proxy_never_shows_its_seed(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    (tmp_path / "h").mkdir(mode=0o700)
    clients.add_client(conn, store, "c", now=datetime.now(UTC), helper_path=tmp_path / "h" / "s")
    proxy = Proxy(tmp_path / "h" / "s", hello=lambda: 1, post=lambda *a: {}, clock=Clock())
    seed = lease_format.read_helper(tmp_path / "h" / "s")[1]
    assert lease_format.encode(seed) not in repr(proxy) and seed.hex() not in repr(proxy)


def test_hello_is_a_control_request_answering_only_the_epoch(tmp_path):
    from comms.runtime.hello import hello_handler
    from comms.transports.telegram.ipc.admin import AdminRouter

    conn = fx.migrated(tmp_path)
    router = AdminRouter(control_handlers={"hello": hello_handler(conn)})
    assert router.dispatch({"control": "hello"}) == {"ok": True, "data": {"security_epoch": 1}}
    bump_security_epoch(conn)
    assert router.dispatch({"control": "hello"})["data"] == {"security_epoch": 2}
    assert router.dispatch({"control": "hello", "args": {"x": 1}})["ok"] is False


async def test_forwarded_results_are_valid_mcp_results(daemon):
    """The stdio handlers return exactly these models; a malformed answer would fail here."""
    from mcp import types

    from comms.mcp.stdio_proxy import build_server

    proxy = _proxy(daemon)
    assert build_server(proxy) is not None
    listed = types.ListToolsResult.model_validate(await asyncio.to_thread(proxy.list_tools))
    called = types.CallToolResult.model_validate(
        await asyncio.to_thread(proxy.call_tool, "comms_capability_list", {})
    )
    assert [t.name for t in listed.tools] == [s.name for s in TOOL_CATALOG]
    assert called.is_error is False and called.structured_content["capabilities"] == []


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.org",
        "http://10.0.0.2:8765",
        "http://example.org:8765",
        "http://127.0.0.1:8765/other",
    ],
)
def test_the_proxy_forwards_only_to_a_loopback_daemon(origin):
    with pytest.raises(ProxyError):
        http_post(origin)
    assert http_post("http://127.0.0.1:8765") and http_post("http://[::1]:8765")
