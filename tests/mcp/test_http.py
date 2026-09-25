"""comms v0.3 Task D28: the stateless Streamable HTTP /mcp with cml1 authentication (N3, A33)."""

import asyncio
import socket
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from comms.core.auth import clients, leases
from comms.core.keys.slots import KeySlotStore
from comms.core.security import security_epoch
from comms.mcp.catalog import TOOL_CATALOG, tools_list_payload
from comms.mcp.dispatch import Dispatcher
from comms.mcp.http import build_http_app, lease_authenticator
from comms.services.registry import ServiceRegistry
from tests.core import schema_fixtures as fx

VERSION = "2026-07-28"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def served(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    (tmp_path / "helper").mkdir(mode=0o700)
    cli = clients.add_client(
        conn, store, "claude-code", now=datetime.now(UTC), helper_path=tmp_path / "helper" / "seed"
    )
    seed = (tmp_path / "helper" / "seed").read_bytes()
    seen = []
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(
            spec.service,
            lambda client, arguments, name=spec.service: (
                seen.append((name, client, arguments)) or {"capabilities": []}
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

    def lease():
        return leases.mint(seed, cli, security_epoch(conn), now=datetime.now(UTC))

    yield {"port": port, "lease": lease, "cli": cli, "seen": seen}
    server.should_exit = True
    await task


async def _post(port, method, params=None, *, token=None, version=VERSION, name=None):
    params = {
        **(params or {}),
        "_meta": {PROTOCOL_VERSION_META_KEY: version, CLIENT_CAPABILITIES_META_KEY: {}},
    }
    headers = {
        "Mcp-Protocol-Version": version,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if name is not None:
        headers["Mcp-Name"] = name
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        return await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )


async def test_unauthenticated_request_refused(served):
    for token in (None, "cml1.forged.token", "tgml1.a.b"):
        response = await _post(served["port"], "tools/list", token=token)
        assert response.status_code == 401 and response.json() == {"error": "unauthorized"}
    assert served["seen"] == []


async def test_tools_list_over_http_matches_catalog(served):
    response = await _post(served["port"], "tools/list", token=served["lease"]())
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    expected = tools_list_payload()
    assert [t["name"] for t in tools] == [e["name"] for e in expected]
    for got, want in zip(tools, expected, strict=True):
        assert (
            got["inputSchema"] == want["inputSchema"]
            and got["outputSchema"] == want["outputSchema"]
        )
        assert got["annotations"]["readOnlyHint"] == want["annotations"]["readOnlyHint"]
        assert got["annotations"]["destructiveHint"] == want["annotations"]["destructiveHint"]


async def test_no_session_state_between_requests(served):
    for _ in range(2):
        response = await _post(
            served["port"],
            "tools/call",
            {"name": "comms_capability_list", "arguments": {}},
            token=served["lease"](),
            name="comms_capability_list",
        )
        assert response.status_code == 200 and "mcp-session-id" not in response.headers
        result = response.json()["result"]
        assert result["isError"] is False and result["structuredContent"]["capabilities"] == []
    assert [(n, c.client_ref) for n, c, _a in served["seen"]] == [
        ("capability.list", served["cli"])
    ] * 2


async def test_protocol_version_2026_07_28_negotiated(served):
    ok = await _post(served["port"], "tools/list", token=served["lease"]())
    assert ok.status_code == 200
    stale = await _post(served["port"], "tools/list", token=served["lease"](), version="1999-01-01")
    assert stale.status_code == 400


async def test_a_tool_error_carries_its_code_only_in_structured_content(served):
    response = await _post(
        served["port"],
        "tools/call",
        {"name": "comms_group_get", "arguments": {"group": "not a ref"}},
        token=served["lease"](),
        name="comms_group_get",
    )
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"] == {"error": {"code": "INVALID_ARGUMENT"}}
    assert "INVALID_ARGUMENT" not in response.json()["result"]["content"][0]["text"]


async def test_mcp_is_the_only_route(served):
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{served['port']}") as client:
        headers = {"Authorization": f"Bearer {served['lease']()}"}
        for path in ("/", "/health", "/mcp/extra", "/.well-known/oauth-protected-resource"):
            assert (await client.get(path, headers=headers)).status_code == 404
