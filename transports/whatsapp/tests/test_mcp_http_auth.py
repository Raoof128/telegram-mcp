"""HTTP-transport auth gate (#19, #18). Auth must live on the transport, not in
the tool signature — a `bearer` tool parameter would be published in the tool's
JSON schema, i.e. the server would ask the model to hand over the secret."""

import asyncio

import pytest

from whatsvault.mcp.http_auth import BearerAuthMiddleware

TOKEN = "s3kr3t-token"


def _drive(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _http_scope(headers):
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }


@pytest.fixture
def downstream():
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app.calls = calls
    return app


def _status(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_missing_authorization_is_rejected(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([]))
    assert _status(sent) == 401
    assert downstream.calls == []


def test_wrong_token_is_rejected(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([("authorization", "Bearer wrong")]))
    assert _status(sent) == 401
    assert downstream.calls == []


def test_wrong_scheme_is_rejected(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([("authorization", f"Basic {TOKEN}")]))
    assert _status(sent) == 401
    assert downstream.calls == []


def test_token_as_raw_header_without_scheme_is_rejected(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([("authorization", TOKEN)]))
    assert _status(sent) == 401
    assert downstream.calls == []


def test_correct_token_reaches_downstream(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([("authorization", f"Bearer {TOKEN}")]))
    assert _status(sent) == 200
    assert downstream.calls == ["/mcp"]


def test_scheme_is_case_insensitive(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([("authorization", f"bearer {TOKEN}")]))
    assert _status(sent) == 200


def test_401_carries_www_authenticate(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([]))
    start = next(m for m in sent if m["type"] == "http.response.start")
    names = {k.decode().lower() for k, _ in start["headers"]}
    assert "www-authenticate" in names


def test_401_body_leaks_nothing(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    sent = _drive(mw, _http_scope([("authorization", "Bearer wrong")]))
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert TOKEN.encode() not in body
    assert b"wrong" not in body


def test_lifespan_passes_through():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    mw = BearerAuthMiddleware(app, TOKEN)
    asyncio.run(mw({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


def test_non_http_scope_is_refused(downstream):
    mw = BearerAuthMiddleware(downstream, TOKEN)
    with pytest.raises(RuntimeError):
        asyncio.run(mw({"type": "websocket"}, None, None))
    assert downstream.calls == []


def test_no_unauthenticated_path_reaches_the_app(downstream):
    """Default-deny stated as the property that matters: whatever the status code,
    an unauthenticated request must never be proxied.

    This previously asserted 401 everywhere. `/.well-known/*` now answers 404 —
    the server is not an OAuth authorization server and saying "unauthorized"
    there made clients parse the error body as protected-resource metadata
    (tests/test_mcp_wellknown.py). The 404 is produced by the middleware and
    never forwarded, so the invariant below is unchanged; only the code differs.
    """
    mw = BearerAuthMiddleware(downstream, TOKEN)
    for path in ("/", "/mcp", "/anything", "/mcp/../mcp"):
        scope = _http_scope([])
        scope["path"] = path
        assert _status(_drive(mw, scope)) == 401, path
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/anything"):
        scope = _http_scope([])
        scope["path"] = path
        assert _status(_drive(mw, scope)) == 404, path
    assert downstream.calls == []
