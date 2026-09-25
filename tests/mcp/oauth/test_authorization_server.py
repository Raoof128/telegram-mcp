"""comms v0.3 Task D32: the OAuth 2.1 authorization server — one predefined client, the full
code / token / refresh lifecycle (A35, G18)."""

import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.applications import Starlette

from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active
from comms.core.security import bump_security_epoch
from comms.mcp.oauth.server import OAuthSettings, build_oauth
from comms.mcp.oauth.tokens import sign_access_token
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

ISSUER = "https://comms.example.org"
RESOURCE = "https://comms.example.org/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
CLIENT_ID = "comms-remote-client"
SCOPE = "comms.full_admin"


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)  # the SDK checks expiry against real time

    def __call__(self):
        return self.now


@pytest.fixture
async def oauth(tmp_path):
    w = comms_world(tmp_path)
    rot.rotate(
        w["writer"],
        w["store"],
        "oauth-signing-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    clock, enabled = Clock(), {"yes": True}
    settings = OAuthSettings(
        issuer=ISSUER,
        resource=RESOURCE,
        client_id=CLIENT_ID,
        redirect_uris=(REDIRECT,),
        owner="owner",
    )
    built = build_oauth(
        w["conn"], w["store"], settings, clock=clock, client_enabled=lambda cid: enabled["yes"]
    )
    transport = httpx.ASGITransport(app=Starlette(routes=built.routes))
    async with httpx.AsyncClient(
        transport=transport, base_url=ISSUER, follow_redirects=False
    ) as http:
        yield {"w": w, "clock": clock, "built": built, "http": http, "enabled": enabled}


def _pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def _authorize(oauth, *, owner_code=None, **over):
    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": SCOPE,
        "state": "xyz",
        "owner_code": owner_code if owner_code is not None else oauth["built"].approvals.issue(),
    }
    params.update(over)
    params = {k: v for k, v in params.items() if v is not None}
    return await oauth["http"].get("/authorize", params=params), verifier


def _code(response):
    if response.status_code not in (302, 303):
        return None, {}
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query.get("code", [None])[0], query


async def _token(oauth, client_id=CLIENT_ID, **form):
    data = {"client_id": client_id, **{k: v for k, v in form.items() if v is not None}}
    return await oauth["http"].post("/token", data=data)


async def _exchange(oauth, code, verifier, **over):
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    form.update(over)
    return await _token(oauth, **form)


async def _tokens(oauth):
    response, verifier = await _authorize(oauth)
    code, _q = _code(response)
    return (await _exchange(oauth, code, verifier)).json()


async def _refresh(oauth, refresh_token, **over):
    return await _token(
        oauth, grant_type="refresh_token", refresh_token=refresh_token, resource=RESOURCE, **over
    )


async def test_metadata_endpoints(oauth):
    meta = (await oauth["http"].get("/.well-known/oauth-authorization-server")).json()
    assert meta["issuer"].rstrip("/") == ISSUER
    assert meta["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert meta["token_endpoint"] == f"{ISSUER}/token"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert "registration_endpoint" not in meta and "revocation_endpoint" not in meta
    paths = sorted(r.path for r in oauth["built"].routes)
    assert paths == ["/.well-known/oauth-authorization-server", "/authorize", "/token"]


async def test_plain_pkce_refused(oauth):
    response, _v = await _authorize(oauth, code_challenge_method="plain")
    code, query = _code(response)
    assert code is None and (response.status_code == 400 or "error" in query)


async def test_owner_approval_is_required_and_one_time(oauth):
    response, _v = await _authorize(oauth, owner_code="not-the-code")
    code, query = _code(response)
    assert code is None and query["error"] == ["access_denied"]
    once = oauth["built"].approvals.issue()
    first, _v1 = await _authorize(oauth, owner_code=once)
    again, _v2 = await _authorize(oauth, owner_code=once)
    assert _code(first)[0] is not None and _code(again)[0] is None
    stale = oauth["built"].approvals.issue()
    oauth["clock"].now += timedelta(minutes=5, seconds=1)
    assert _code((await _authorize(oauth, owner_code=stale))[0])[0] is None


async def test_code_single_use_expiry_and_binding(oauth):
    response, verifier = await _authorize(oauth)
    code, query = _code(response)
    assert query["state"] == ["xyz"] and len(base64.urlsafe_b64decode(code + "==")) >= 32
    assert (await _exchange(oauth, code, verifier)).status_code == 200
    again = await _exchange(oauth, code, verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"  # single use
    for over in (
        {"redirect_uri": "https://evil.example/cb"},
        {"resource": "https://other.example/mcp"},
        {"code_verifier": "x" * 43},
    ):
        response, verifier = await _authorize(oauth)
        code, _q = _code(response)
        assert (await _exchange(oauth, code, verifier, **over)).status_code == 400, over
    response, verifier = await _authorize(oauth)
    code, _q = _code(response)
    other = await _token(
        oauth,
        client_id="someone-else",
        grant_type="authorization_code",
        code=code,
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        resource=RESOURCE,
    )
    assert other.status_code in (400, 401)
    response, verifier = await _authorize(oauth)
    code, _q = _code(response)
    oauth["clock"].now += timedelta(seconds=61)
    assert (await _exchange(oauth, code, verifier)).json()["error"] == "invalid_grant"  # 60 s


async def test_access_token_claims(oauth):
    tokens = await _tokens(oauth)
    header, payload, _sig = tokens["access_token"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert json.loads(base64.urlsafe_b64decode(header + "=="))["alg"] == "EdDSA"
    assert set(claims) == {"iss", "sub", "aud", "resource", "scope", "exp", "iat", "jti", "sec"}
    assert claims["sub"] == "owner" and claims["scope"] == SCOPE and claims["resource"] == RESOURCE
    assert claims["exp"] - claims["iat"] == 600 and tokens["expires_in"] == 600
    assert oauth["built"].verify(tokens["access_token"]) is not None


async def test_wrong_subject_refused(oauth):
    tokens = await _tokens(oauth)
    payload = tokens["access_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    key, _key_id = load_active(oauth["w"]["conn"], oauth["w"]["store"], "oauth-signing-key")
    forged = sign_access_token(key, {**claims, "sub": "someone-else"})
    assert oauth["built"].verify(forged) is None


async def test_unknown_scope_not_upgraded(oauth):
    response, _v = await _authorize(oauth, scope="comms.full_admin comms.everything")
    code, query = _code(response)
    assert code is None and query["error"] == ["invalid_scope"]
    tokens = await _tokens(oauth)
    upgraded = await _refresh(oauth, tokens["refresh_token"], scope="comms.full_admin comms.more")
    assert upgraded.status_code == 400


async def test_resource_parameter_required(oauth):
    response, _v = await _authorize(oauth, resource=None)
    code, _query = _code(response)
    assert code is None
    response, verifier = await _authorize(oauth)
    code, _q = _code(response)
    assert (await _exchange(oauth, code, verifier, resource=None)).status_code == 400


async def test_refresh_rotation_and_reuse_revokes_family(oauth):
    first = await _tokens(oauth)
    second = (await _refresh(oauth, first["refresh_token"])).json()
    assert second["refresh_token"] != first["refresh_token"] and "access_token" in second
    reused = await _refresh(oauth, first["refresh_token"])
    assert reused.json()["error"] == "invalid_grant"
    after = await _refresh(oauth, second["refresh_token"])
    assert after.json()["error"] == "invalid_grant"  # the whole family is revoked
    stored = oauth["w"]["conn"].execute("SELECT token_hash FROM oauth_refresh_tokens").fetchall()
    assert stored and all(first["refresh_token"] not in row[0] for row in stored)  # hashed


async def test_epoch_bump_and_client_disable_invalidate(oauth):
    tokens = await _tokens(oauth)
    bump_security_epoch(oauth["w"]["conn"])
    assert oauth["built"].verify(tokens["access_token"]) is None
    assert (await _refresh(oauth, tokens["refresh_token"])).status_code == 400
    fresh = await _tokens(oauth)
    oauth["enabled"]["yes"] = False
    assert oauth["built"].verify(fresh["access_token"]) is None


@pytest.mark.parametrize("alg", ["none", "HS256", "RS256", "EdDSA "])
async def test_alg_none_and_hs256_refused(oauth, alg):
    tokens = await _tokens(oauth)
    _h, payload, sig = tokens["access_token"].split(".")
    header = json.dumps({"alg": alg, "typ": "at+jwt"}).encode()
    encoded = base64.urlsafe_b64encode(header).rstrip(b"=").decode()
    assert oauth["built"].verify(f"{encoded}.{payload}.{sig}") is None
    assert oauth["built"].verify(f"{encoded}.{payload}.") is None


async def test_no_code_or_token_in_logs_or_errors(oauth, caplog):
    caplog.set_level(logging.DEBUG)
    response, verifier = await _authorize(oauth)
    code, _q = _code(response)
    tokens = (await _exchange(oauth, code, verifier)).json()
    bad = await _exchange(oauth, code, verifier)
    reuse = await _refresh(oauth, tokens["refresh_token"] + "x")
    text = caplog.text + bad.text + reuse.text + repr(oauth["built"])
    for secret in (code, tokens["access_token"], tokens["refresh_token"], verifier):
        assert secret not in text
