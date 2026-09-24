"""OAuth-discovery paths answer "nothing here", not "unauthorized".

This server authenticates with a static bearer token and is not an OAuth
authorization server. A client that probes `/.well-known/oauth-protected-resource`
got the auth gate's `401 {"error":"unauthorized"}`, and clients parse that body as
protected-resource metadata: OpenAI's tunnel-client reported
`invalid metadata … protected resource metadata missing resource` and held
readiness degraded. A 404 says the same thing truthfully and is what a server
without OAuth metadata is supposed to return.

The danger in carving any hole in a default-deny gate is that it becomes a
bypass. It cannot here, because the middleware answers these paths ITSELF and
never forwards them to the inner app — there is no request shape that reaches a
tool without a valid token. These tests are written as the attacker's goal:
reach the MCP app through a path that starts with `/.well-known/`.
"""

import asyncio
import json

import pytest

from whatsvault.mcp.http_auth import BearerAuthMiddleware

TOKEN = "t" * 64


class _Spy:
    """Inner app that records every request that reaches it."""

    def __init__(self):
        self.seen = []

    async def __call__(self, scope, receive, send):
        self.seen.append(scope.get("path"))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"REACHED-THE-APP"})


def _call(app, path, auth=None, method="GET"):
    """Drive the middleware directly, matching test_mcp_http_auth.py: there is no
    async pytest plugin here, so the event loop is owned by the helper."""
    headers = [(b"host", b"127.0.0.1:8765")]
    if auth:
        headers.append((b"authorization", auth.encode()))
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "path": path, "method": method, "headers": headers}
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@pytest.fixture
def app():
    spy = _Spy()
    return BearerAuthMiddleware(spy, TOKEN), spy


# ---- the behaviour the tunnel needs ---------------------------------------------
def test_metadata_probe_is_404_not_401(app):
    mw, _ = app
    status, body = _call(mw, "/.well-known/oauth-protected-resource")
    assert status == 404
    assert b"unauthorized" not in body


def test_the_404_body_is_not_mistakable_for_metadata(app):
    """The whole defect was a body that parsed as metadata. Whatever is returned
    must not look like a protected-resource document."""
    mw, _ = app
    _, body = _call(mw, "/.well-known/oauth-protected-resource/mcp")
    payload = json.loads(body)
    assert "resource" not in payload
    assert "authorization_servers" not in payload


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/.well-known/openid-configuration",
    ],
)
def test_every_discovery_candidate_answers_404(app, path):
    """tunnel-client probes several candidates and only treats the server as
    "plainly not OAuth" when they all 404."""
    mw, _ = app
    status, _ = _call(mw, path)
    assert status == 404


# ---- and it is not a bypass ------------------------------------------------------
def test_a_metadata_path_never_reaches_the_app(app):
    """Answered by the middleware itself. If it were merely exempted from the
    token check and forwarded, this would be a hole straight through."""
    mw, spy = app
    _call(mw, "/.well-known/oauth-protected-resource")
    assert spy.seen == []


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/../mcp",
        "/.well-known/oauth-protected-resource/../../mcp",
        "/.well-known/%2e%2e/mcp",
        "/.well-known/oauth-protected-resource/../mcp",
    ],
)
def test_traversal_out_of_the_metadata_prefix_never_reaches_the_app(app, path):
    """A path that starts with the exempt prefix and then climbs out must not be
    forwarded. Nothing under the prefix is ever proxied, so it cannot be."""
    mw, spy = app
    status, body = _call(mw, path)
    assert spy.seen == [], f"{path} reached the app"
    assert b"REACHED-THE-APP" not in body
    assert status in (401, 404)


def test_a_lookalike_prefix_is_still_authenticated(app):
    """`/.well-known-not-really/` is not the metadata prefix and must stay behind
    the gate."""
    mw, spy = app
    status, _ = _call(mw, "/.well-known-not-really/mcp")
    assert status == 401 and spy.seen == []


def test_the_mcp_endpoint_is_unaffected(app):
    mw, spy = app
    assert (_call(mw, "/mcp", method="POST"))[0] == 401
    assert spy.seen == []
    status, body = _call(mw, "/mcp", auth=f"Bearer {TOKEN}", method="POST")
    assert status == 200 and b"REACHED-THE-APP" in body


def test_a_valid_token_does_not_unlock_the_metadata_paths_either(app):
    """They are 404 because the server has no OAuth metadata — not because the
    caller was unauthenticated. The answer must not depend on the token."""
    mw, spy = app
    with_token = _call(mw, "/.well-known/oauth-protected-resource", auth=f"Bearer {TOKEN}")
    without = _call(mw, "/.well-known/oauth-protected-resource")
    assert with_token == without
    assert spy.seen == []
