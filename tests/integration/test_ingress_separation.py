"""comms v0.3 Task D34: three isolated listeners; the remote MCP ingress carries the OAuth route
set (A36, G18)."""

import asyncio
import base64
import hashlib
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from comms.core import refs
from comms.core.auth import clients, lease_format
from comms.core.keys import rotate as rot
from comms.core.security import security_epoch
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import Dispatcher
from comms.mcp.http import lease_authenticator
from comms.mcp.oauth.server import OAuthSettings, build_oauth
from comms.runtime.listeners import REMOTE_PATHS, RemoteListener, build_listeners
from comms.services.registry import ServiceRegistry
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

ISSUER = "https://comms.example.org"
RESOURCE = "https://comms.example.org/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERSION = "2026-07-28"
PROBES = (
    "/",
    "/mcp",
    "/mcp/",
    "/webhooks/meta",
    "/authorize",
    "/token",
    "/health",
    "/register",
    "/revoke",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/admin",
    "/metrics",
)


@asynccontextmanager
async def _lifespan(app):
    """Run an ASGI app's lifespan (the SDK's session manager starts once per app)."""
    inbox, outbox = asyncio.Queue(), asyncio.Queue()
    await inbox.put({"type": "lifespan.startup"})
    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put)
    )
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await task


@pytest.fixture
async def listeners(tmp_path):
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
    _cli, seed = lease_format.read_helper(tmp_path / "h" / "s")
    seen = []
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(
            spec.service,
            lambda client, arguments, name=spec.service: (
                seen.append((name, client.auth_kind)) or {"capabilities": []}
            ),
        )
    settings = OAuthSettings(
        issuer=ISSUER,
        resource=RESOURCE,
        client_id="remote-client",
        redirect_uris=(REDIRECT,),
        owner="owner",
    )
    oauth = build_oauth(
        w["conn"],
        w["store"],
        settings,
        clock=lambda: datetime.now(UTC),
        client_enabled=lambda c: True,
    )
    webhook = WebhookIngress(
        app_secret=b"s" * 32, verify_token="v" * 32, accept=lambda *a: None, clock=lambda: 0.0
    )
    built = build_listeners(
        Dispatcher(services),
        local_authenticate=lease_authenticator(w["conn"], w["store"], lambda: datetime.now(UTC)),
        host="127.0.0.1",
        local_port=8765,
        remote=RemoteListener(
            oauth=oauth, settings=settings, client_ref=refs.mint("client"), port=8767
        ),
        webhook=webhook,
    )
    lease = lease_format.mint(seed, local_cli, security_epoch(w["conn"]), now=datetime.now(UTC))
    async with _lifespan(built.local), _lifespan(built.remote):
        yield {"built": built, "seen": seen, "oauth": oauth, "lease": lease}


def _client(app, base):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base, follow_redirects=False
    )


async def _status(app, base, path, method="GET"):
    async with _client(app, base) as client:
        return (await client.request(method, path)).status_code


async def _mcp(app, base, token):
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
    async with _client(app, base) as client:
        return await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        )


async def test_webhook_listener_has_no_route_to_mcp_dispatch(listeners):
    webhook = listeners["built"].webhook
    for path in PROBES:
        for method in ("GET", "POST"):
            status = await _status(webhook, "http://127.0.0.1:8768", path, method)
            if path != "/webhooks/meta":
                assert status == 404, (path, method, status)
    assert listeners["seen"] == []


async def test_mcp_listeners_have_no_webhook_route(listeners):
    local, remote = listeners["built"].local, listeners["built"].remote
    for app, base in ((local, "http://127.0.0.1:8765"), (remote, ISSUER)):
        for method in ("GET", "POST"):
            assert await _status(app, base, "/webhooks/meta", method) == 404


async def test_the_local_listener_serves_only_mcp(listeners):
    local = listeners["built"].local
    for path in PROBES:
        if path != "/mcp":
            assert await _status(local, "http://127.0.0.1:8765", path) in (401, 404), path


async def test_remote_listener_route_set_is_exactly_the_oauth_set_plus_mcp(listeners):
    remote = listeners["built"].remote
    assert set(REMOTE_PATHS) == {
        "/mcp",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/authorize",
        "/token",
    }
    for path in PROBES:
        status = await _status(remote, ISSUER, path)
        assert (status == 404) == (path not in REMOTE_PATHS), (path, status)


async def test_remote_oauth_flow_completes_through_the_remote_listener_only(listeners):
    remote, local = listeners["built"].remote, listeners["built"].local
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    params = {
        "response_type": "code",
        "client_id": "remote-client",
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": "comms.full_admin",
        "state": "s",
        "owner_code": listeners["oauth"].approvals.issue(),
    }
    async with _client(remote, ISSUER) as client:
        meta = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert meta["resource"].rstrip("/") == RESOURCE and meta["authorization_servers"]
        code = parse_qs(
            urlparse((await client.get("/authorize", params=params)).headers["location"]).query
        )["code"][0]
        tokens = (
            await client.post(
                "/token",
                data={
                    "client_id": "remote-client",
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "code_verifier": verifier,
                    "resource": RESOURCE,
                },
            )
        ).json()
    response = await _mcp(remote, ISSUER, tokens["access_token"])
    assert response.status_code == 200 and listeners["seen"] == [("capability.list", "oauth")]
    assert await _status(local, "http://127.0.0.1:8765", "/authorize") == 404
    assert (await _mcp(local, "http://127.0.0.1:8765", tokens["access_token"])).status_code == 401


async def test_remote_listener_refuses_cml1(listeners):
    assert (await _mcp(listeners["built"].remote, ISSUER, listeners["lease"])).status_code == 401
    ok = await _mcp(listeners["built"].local, "http://127.0.0.1:8765", listeners["lease"])
    assert ok.status_code == 200 and listeners["seen"] == [("capability.list", "cml1")]
