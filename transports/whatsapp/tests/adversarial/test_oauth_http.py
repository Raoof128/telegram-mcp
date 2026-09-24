"""The OAuth HTTP surface, attacked.

The grant machinery is tested next door. This file is about the thing that
worries me more: the OAuth endpoints must be reachable *without* a token — that
is what an authorization server is — so the request router in front of the MCP
app now has an unauthenticated region. Every test here tries to use that region
to reach a tool.

The second theme is mode. Public OAuth exists only when the operator has
deliberately deployed behind a public URL. On loopback — the default, and how
Claude Code talks to this vault — none of it is mounted, so a local deployment
gains no new attack surface from a feature it is not using.
"""

import asyncio
import json
import os
import urllib.parse

import pytest

from apps.mcp import server
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import oauth

TOKEN = "s" * 64
PUBLIC = "https://vault.example.com"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


class _Spy:
    def __init__(self):
        self.seen = []

    async def __call__(self, scope, receive, send):
        self.seen.append(scope.get("path"))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"REACHED-THE-MCP-APP"})


def _drive(app, method, path, *, query="", body=b"", headers=None):
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"vault.example.com"), *(headers or [])],
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict(start.get("headers") or []), payload


@pytest.fixture
def control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(conn, "control")
    return conn


@pytest.fixture
def public(control):
    """The app as deployed behind a public URL."""
    spy = _Spy()
    return server.build_oauth_app(spy, control, TOKEN, public_url=PUBLIC), control, spy


@pytest.fixture
def loopback(control):
    """The app as deployed locally: no OAuth at all."""
    spy = _Spy()
    return server.build_oauth_app(spy, control, TOKEN, public_url=None), control, spy


# =============================================================================
# Goal: reach a tool through the unauthenticated OAuth region.
# =============================================================================
@pytest.mark.parametrize(
    "path",
    [
        "/oauth/../mcp",
        "/oauth/authorize/../../mcp",
        "/oauth/token/../../mcp",
        "/oauth//mcp",
        "/oauth/%2e%2e/mcp",
        "/.well-known/../mcp",
    ],
)
def test_no_oauth_path_can_be_walked_back_to_the_mcp_app(public, path):
    app, _, spy = public
    status, _, body = _drive(app, "GET", path)
    assert spy.seen == [], f"{path} reached the MCP app"
    assert b"REACHED-THE-MCP-APP" not in body
    assert status in (401, 404, 405)


def test_the_mcp_app_still_requires_a_token_in_public_mode(public):
    app, _, spy = public
    status, headers, _ = _drive(app, "POST", "/mcp")
    assert status == 401 and spy.seen == []
    # RFC 9728: the 401 must point the client at the metadata so it can discover
    # the authorization server. Without this ChatGPT cannot start the flow.
    www = headers[b"www-authenticate"].decode()
    assert "resource_metadata=" in www and PUBLIC in www


def test_a_valid_static_token_still_works_in_public_mode(public):
    """The local operator's token is not disabled by going public."""
    app, _, spy = public
    status, _, body = _drive(app, "POST", "/mcp", headers=[(b"authorization", f"Bearer {TOKEN}".encode())])
    assert status == 200 and b"REACHED-THE-MCP-APP" in body and spy.seen == ["/mcp"]


# =============================================================================
# Goal: use the OAuth surface on a deployment that never enabled it.
# =============================================================================
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/oauth/register"),
        ("GET", "/oauth/authorize"),
        ("POST", "/oauth/token"),
        ("GET", "/oauth/poll"),
        ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/.well-known/oauth-protected-resource"),
    ],
)
def test_loopback_mounts_no_oauth_surface_at_all(loopback, method, path):
    app, _, spy = loopback
    status, _, _ = _drive(app, method, path)
    assert status == 404, f"{path} is mounted on a loopback deployment"
    assert spy.seen == []


def test_loopback_401_does_not_advertise_an_authorization_server(loopback):
    app, _, _ = loopback
    _, headers, _ = _drive(app, "POST", "/mcp")
    assert b"resource_metadata" not in headers.get(b"www-authenticate", b"")


# =============================================================================
# Goal: get a token through the HTTP surface without approval.
# =============================================================================
def _register(app):
    status, _, body = _drive(
        app,
        "POST",
        "/oauth/register",
        body=json.dumps({"client_name": "ChatGPT", "redirect_uris": [REDIRECT]}).encode(),
        headers=[(b"content-type", b"application/json")],
    )
    assert status == 201, body
    return json.loads(body)["client_id"]


def _begin(app, client_id, challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"):
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        }
    )
    return _drive(app, "GET", "/oauth/authorize", query=q)


def test_the_consent_page_never_asks_for_a_secret(public):
    """The design decision this whole flow rests on. A public page with a
    password field is a phishing target and a brute-force target; approval
    happens in the terminal instead."""
    app, _, _ = public
    status, _, body = _begin(app, _register(app))
    assert status == 200
    page = body.decode().lower()
    assert 'type="password"' not in page
    assert "<form" not in page, "the consent page must not post credentials anywhere"
    for word in ("password", "token", "secret", "api key"):
        assert f'name="{word}"' not in page
    assert "oauth-approve" in page, "the page must tell the operator how to approve"


def test_polling_before_approval_never_returns_a_code(public):
    app, control, _ = public
    _, _, body = _begin(app, _register(app))
    request_id = json.loads(_drive(app, "GET", "/oauth/poll", query=f"request_id={_rid(body)}")[2])
    assert request_id["ready"] is False
    assert "code" not in json.dumps(request_id)


def _rid(page_bytes):
    """The consent page embeds its request id for the poller."""
    page = page_bytes.decode()
    marker = 'data-request-id="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def test_the_full_flow_works_once_the_operator_approves(public):
    """The positive control: everything above must fail for the right reason, not
    because the flow is simply broken."""
    import base64
    import hashlib
    import secrets

    app, control, spy = public
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    client_id = _register(app)
    _, _, page = _begin(app, client_id, challenge)

    pending = oauth.pending_requests(control, now_ms=_now())
    assert len(pending) == 1
    oauth.approve(control, user_code=pending[0]["user_code"], now_ms=_now())

    ready = json.loads(_drive(app, "GET", "/oauth/poll", query=f"request_id={_rid(page)}")[2])
    assert ready["ready"] is True
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(ready["redirect"]).query))["code"]

    status, _, body = _drive(
        app,
        "POST",
        "/oauth/token",
        body=urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
            }
        ).encode(),
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
    )
    assert status == 200, body
    granted = json.loads(body)
    assert granted["token_type"] == "Bearer" and granted["scope"] == oauth.READ_ONLY_SCOPE

    # and the issued token actually opens the MCP surface
    status, _, body = _drive(
        app,
        "POST",
        "/mcp",
        headers=[(b"authorization", f"Bearer {granted['access_token']}".encode())],
    )
    assert status == 200 and b"REACHED-THE-MCP-APP" in body


def _now():
    import time

    return int(time.time() * 1000)


# =============================================================================
# Goal: make the endpoints misbehave with hostile input.
# =============================================================================
@pytest.mark.parametrize(
    "body",
    [b"", b"not json", b"[]", b"null", b'{"redirect_uris": "not-a-list"}', b'{"redirect_uris": []}'],
)
def test_registration_rejects_junk_without_a_traceback(public, body):
    app, _, _ = public
    status, _, out = _drive(
        app, "POST", "/oauth/register", body=body, headers=[(b"content-type", b"application/json")]
    )
    assert status == 400
    assert b"Traceback" not in out and b"sqlcipher" not in out.lower()


def test_authorize_with_an_unregistered_redirect_does_not_redirect(public):
    """The error must be shown locally, never bounced to the attacker's URI —
    redirecting an error to an unvalidated destination is the open redirect."""
    app, _, _ = public
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": _register(app),
            "redirect_uri": "https://evil.example.com/steal",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
        }
    )
    status, headers, _ = _drive(app, "GET", "/oauth/authorize", query=q)
    assert status == 400
    assert b"location" not in headers


def test_token_endpoint_reports_oauth_errors_not_stack_traces(public):
    app, _, _ = public
    status, _, body = _drive(
        app,
        "POST",
        "/oauth/token",
        body=urllib.parse.urlencode(
            {"grant_type": "authorization_code", "code": "nope", "client_id": "wvc_x"}
        ).encode(),
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
    )
    assert status == 400
    assert json.loads(body)["error"] == "invalid_grant"


def test_an_unsupported_grant_type_is_refused(public):
    app, _, _ = public
    status, _, body = _drive(
        app,
        "POST",
        "/oauth/token",
        body=urllib.parse.urlencode({"grant_type": "password", "username": "a", "password": "b"}).encode(),
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
    )
    assert status == 400 and json.loads(body)["error"] == "unsupported_grant_type"


# =============================================================================
# Goal: learn something from the metadata that helps an attack.
# =============================================================================
def test_metadata_advertises_only_what_is_supported(public):
    app, _, _ = public
    meta = json.loads(_drive(app, "GET", "/.well-known/oauth-authorization-server")[2])
    assert meta["code_challenge_methods_supported"] == ["S256"], "must not advertise plain"
    assert meta["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert meta["response_types_supported"] == ["code"]
    for url in (meta["authorization_endpoint"], meta["token_endpoint"], meta["issuer"]):
        assert url.startswith("https://"), url


def test_protected_resource_metadata_points_at_this_server(public):
    app, _, _ = public
    meta = json.loads(_drive(app, "GET", "/.well-known/oauth-protected-resource")[2])
    assert meta["resource"].startswith(PUBLIC)
    assert meta["authorization_servers"] == [PUBLIC]


def test_metadata_leaks_no_token_and_no_vault_content(public):
    app, control, _ = public
    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
    ):
        body = _drive(app, "GET", path)[2].decode()
        assert TOKEN not in body
        assert "whatsvault.mcp.token" not in body


# =============================================================================
# Goal: reach the transport from a host it was not published at.
# =============================================================================
def test_the_public_host_is_pinned_not_the_protection_disabled():
    """DNS-rebinding protection pins the Host header to loopback, so a valid
    OAuth token still got `421 Invalid Host header` over the public URL — the
    flow completed and then the transport refused the request.

    The fix must add the published host, not switch the protection off: a
    wildcard would let any hostile page that resolves to this server drive it.
    """
    settings = server.transport_security_settings(port=8765, public_url=PUBLIC)
    assert settings.enable_dns_rebinding_protection is True
    assert "vault.example.com" in settings.allowed_hosts
    assert PUBLIC in settings.allowed_origins
    # loopback keeps working for the local connectors
    assert "127.0.0.1:8765" in settings.allowed_hosts
    for wildcard in ("*", "*.example.com", ""):
        assert wildcard not in settings.allowed_hosts
        assert wildcard not in settings.allowed_origins


def test_loopback_only_settings_do_not_admit_any_public_host():
    settings = server.transport_security_settings(port=8765)
    assert settings.enable_dns_rebinding_protection is True
    assert all("." not in h or h.startswith("127.") for h in settings.allowed_hosts)


def test_build_app_pins_the_host_it_was_published_at(tmp_path):
    """The wiring, not just the helper: build_app must pass the public URL down
    to the transport or the 421 comes back."""
    import inspect

    src = inspect.getsource(server.build_app)
    assert "public_url=public_url" in src or "public_url)" in src
    assert "transport_security_settings(port, public_url" in src
