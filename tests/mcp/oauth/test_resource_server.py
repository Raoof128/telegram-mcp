"""comms v0.3 Task D33: /mcp validates OAuth tokens on every remote request (A35, G18)."""

import asyncio
import os
import socket
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from comms.core import refs
from comms.core.auth import clients, lease_format
from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active
from comms.core.security import security_epoch
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import Dispatcher
from comms.mcp.http import build_http_app, oauth_authenticator
from comms.mcp.oauth.server import OAuthSettings, build_oauth
from comms.mcp.oauth.tokens import sign_access_token
from comms.services.registry import ServiceRegistry
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

ISSUER = "https://comms.example.org"
RESOURCE = "https://comms.example.org/mcp"
VERSION = "2026-07-28"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def remote(tmp_path):
    w = comms_world(tmp_path)
    rot.rotate(
        w["writer"],
        w["store"],
        "oauth-signing-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    (tmp_path / "h").mkdir(mode=0o700)
    local_cli = clients.add_client(
        w["conn"], w["store"], "local", now=datetime.now(UTC), helper_path=tmp_path / "h" / "s"
    )
    settings = OAuthSettings(
        issuer=ISSUER,
        resource=RESOURCE,
        client_id="remote-client",
        redirect_uris=("https://claude.ai/cb",),
        owner="owner",
    )
    built = build_oauth(
        w["conn"],
        w["store"],
        settings,
        clock=lambda: datetime.now(UTC),
        client_enabled=lambda c: True,
    )
    remote_cli = refs.mint("client")
    seen = []
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(
            spec.service,
            lambda client, arguments, name=spec.service: (
                seen.append((name, client.client_ref, client.auth_kind)) or {"capabilities": []}
            ),
        )
    port = _free_port()
    app = build_http_app(
        Dispatcher(services), oauth_authenticator(built, remote_cli), host="127.0.0.1", port=port
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    key, _kid = load_active(w["conn"], w["store"], "oauth-signing-key")
    iat = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": ISSUER,
        "sub": "owner",
        "aud": RESOURCE,
        "resource": RESOURCE,
        "scope": "comms.full_admin",
        "exp": iat + 600,
        "iat": iat,
        "jti": "j1",
        "sec": security_epoch(w["conn"]),
    }
    _cli, seed = lease_format.read_helper(tmp_path / "h" / "s")
    lease = lease_format.mint(seed, local_cli, security_epoch(w["conn"]), now=datetime.now(UTC))
    yield {
        "port": port,
        "seen": seen,
        "key": key,
        "claims": claims,
        "remote_cli": remote_cli,
        "lease": lease,
    }
    server.should_exit = True
    await task


async def _call(port, token):
    params = {
        "name": "comms_capability_list",
        "arguments": {},
        "_meta": {PROTOCOL_VERSION_META_KEY: VERSION, CLIENT_CAPABILITIES_META_KEY: {}},
    }
    headers = {
        "Mcp-Protocol-Version": VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "comms_capability_list",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        return await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        )


async def test_valid_token_reaches_dispatch(remote):
    response = await _call(remote["port"], sign_access_token(remote["key"], remote["claims"]))
    assert response.status_code == 200 and response.json()["result"]["isError"] is False
    assert remote["seen"] == [("capability.list", remote["remote_cli"], "oauth")]


@pytest.mark.parametrize(
    "claim, value",
    [
        ("iss", "https://evil.example"),
        ("sub", "someone-else"),
        ("aud", "https://other.example/mcp"),
        ("resource", "https://other.example/mcp"),
        ("scope", "comms.read"),
        ("exp", 1),
        ("sec", 999),
    ],
)
async def test_each_claim_failure_is_refused(remote, claim, value):
    token = sign_access_token(remote["key"], {**remote["claims"], claim: value})
    response = await _call(remote["port"], token)
    assert response.status_code == 401 and remote["seen"] == []


async def test_a_token_signed_by_another_key_is_refused(remote):
    response = await _call(remote["port"], sign_access_token(os.urandom(32), remote["claims"]))
    assert response.status_code == 401 and remote["seen"] == []


async def test_cml1_not_accepted_on_remote_listener(remote):
    response = await _call(remote["port"], remote["lease"])
    assert response.status_code == 401 and remote["seen"] == []
