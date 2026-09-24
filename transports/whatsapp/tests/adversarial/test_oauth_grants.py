"""The OAuth authorization server, attacked.

Going public changes what the auth layer is for. On loopback, a static bearer
token was enough: reaching the port already meant running as the user. Behind a
public URL the authorization server IS the boundary — everyone on the internet
can now reach `/oauth/authorize`, and the only thing between them and the vault
is the code below.

Every test here is written as something an attacker wants to achieve, not as a
description of the implementation, so a refactor cannot quietly reopen a hole
while keeping the file green.

The central design decision under test: **no secret is ever typed into the
consent page.** A public form asking for a password is a phishing and
brute-force target, and the whole project already holds that approval must
happen on a channel the model — or in this case the internet — cannot reach.
Authorization is granted out of band, from the terminal, by an operator who can
read the vault's own database.
"""

import base64
import hashlib
import os
import secrets

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import oauth

NOW = 1_800_000_000_000
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


def _verifier_and_challenge():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.fixture
def control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    return conn


@pytest.fixture
def client(control):
    return oauth.register_client(control, client_name="ChatGPT", redirect_uris=[REDIRECT], now_ms=NOW)


def _authorize(control, client, challenge, redirect=REDIRECT, now_ms=NOW):
    return oauth.begin_authorization(
        control,
        client_id=client["client_id"],
        redirect_uri=redirect,
        code_challenge=challenge,
        code_challenge_method="S256",
        state="xyz",
        scope=None,
        now_ms=now_ms,
    )


def _approved_code(control, client, challenge, now_ms=NOW):
    pending = _authorize(control, client, challenge, now_ms=now_ms)
    oauth.approve(control, user_code=pending["user_code"], now_ms=now_ms)
    return oauth.poll(control, request_id=pending["request_id"], now_ms=now_ms)["code"]


# =============================================================================
# Goal: get a token without the operator ever approving anything.
# =============================================================================
def test_an_unapproved_request_yields_no_code(control, client):
    _, challenge = _verifier_and_challenge()
    pending = _authorize(control, client, challenge)
    assert oauth.poll(control, request_id=pending["request_id"], now_ms=NOW) is None


def test_polling_forever_does_not_eventually_succeed(control, client):
    """No timeout, retry count, or clock advance substitutes for approval."""
    _, challenge = _verifier_and_challenge()
    pending = _authorize(control, client, challenge)
    for step in (0, 1_000, 60_000, oauth.PENDING_TTL_MS - 1):
        assert oauth.poll(control, request_id=pending["request_id"], now_ms=NOW + step) is None


def test_an_expired_request_cannot_be_approved(control, client):
    _, challenge = _verifier_and_challenge()
    pending = _authorize(control, client, challenge)
    late = NOW + oauth.PENDING_TTL_MS + 1
    with pytest.raises(oauth.OAuthError):
        oauth.approve(control, user_code=pending["user_code"], now_ms=late)
    assert oauth.poll(control, request_id=pending["request_id"], now_ms=late) is None


def test_guessing_a_user_code_is_not_feasible(control, client):
    """The user code is what an attacker would brute-force to self-approve, so it
    must carry real entropy and not be a counter or a timestamp.

    Generated directly rather than through begin_authorization: that path is
    throttled at MAX_PENDING live requests, which is a different defence tested
    in test_redteam_public.py.
    """
    codes = {oauth._new_user_code() for _ in range(50)}
    assert len(codes) == 50, "user codes repeat"
    alphabet = set("".join(codes)) - {"-"}
    body = len(next(iter(codes)).replace("-", ""))
    assert len(alphabet) ** body >= 2**40, f"only {len(alphabet)}^{body} possibilities"


def test_wrong_user_code_is_refused(control, client):
    _, challenge = _verifier_and_challenge()
    _authorize(control, client, challenge)
    with pytest.raises(oauth.OAuthError):
        oauth.approve(control, user_code="AAAA-AAAA", now_ms=NOW)


def test_approving_twice_does_not_mint_a_second_code(control, client):
    _, challenge = _verifier_and_challenge()
    pending = _authorize(control, client, challenge)
    oauth.approve(control, user_code=pending["user_code"], now_ms=NOW)
    with pytest.raises(oauth.OAuthError):
        oauth.approve(control, user_code=pending["user_code"], now_ms=NOW)


# =============================================================================
# Goal: skip PKCE, or bypass it.
# =============================================================================
def test_authorization_without_pkce_is_refused(control, client):
    with pytest.raises(oauth.OAuthError):
        oauth.begin_authorization(
            control,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_challenge=None,
            code_challenge_method=None,
            state="xyz",
            scope=None,
            now_ms=NOW,
        )


def test_plain_pkce_is_refused(control, client):
    """`plain` makes the challenge equal the verifier, so intercepting the
    authorization request is enough to complete the exchange. OAuth 2.1 keeps
    S256 only."""
    with pytest.raises(oauth.OAuthError):
        oauth.begin_authorization(
            control,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_challenge="whatever",
            code_challenge_method="plain",
            state="xyz",
            scope=None,
            now_ms=NOW,
        )


def test_a_stolen_code_is_useless_without_the_verifier(control, client):
    """The attack PKCE exists to stop: the code leaks through a redirect, a log,
    or the browser's history, and is redeemed by someone else."""
    _, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(
            control,
            code=code,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_verifier=secrets.token_urlsafe(64),  # attacker's own
            now_ms=NOW,
        )


def test_the_verifier_must_actually_hash_to_the_challenge(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(
            control,
            code=code,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_verifier=verifier[:-1],  # one character off
            now_ms=NOW,
        )
    # and the real one still works, so the check is not simply always-fail
    assert oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )["access_token"]


# =============================================================================
# Goal: replay or reuse an authorization code.
# =============================================================================
def test_a_code_cannot_be_redeemed_twice(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    kw = {
        "code": code,
        "client_id": client["client_id"],
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }
    assert oauth.exchange_code(control, now_ms=NOW, **kw)["access_token"]
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(control, now_ms=NOW, **kw)


def test_a_code_expires_quickly(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    assert oauth.CODE_TTL_MS <= 120_000, "an authorization code must be short-lived"
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(
            control,
            code=code,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_verifier=verifier,
            now_ms=NOW + oauth.CODE_TTL_MS + 1,
        )


def test_a_code_is_bound_to_the_client_that_requested_it(control, client):
    other = oauth.register_client(control, client_name="attacker", redirect_uris=[REDIRECT], now_ms=NOW)
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(
            control,
            code=code,
            client_id=other["client_id"],
            redirect_uri=REDIRECT,
            code_verifier=verifier,
            now_ms=NOW,
        )


def test_a_code_is_bound_to_the_redirect_uri_it_was_issued_for(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code(
            control,
            code=code,
            client_id=client["client_id"],
            redirect_uri="https://chatgpt.com/other",
            code_verifier=verifier,
            now_ms=NOW,
        )


# =============================================================================
# Goal: turn the authorize endpoint into an open redirect.
# =============================================================================
def test_an_unregistered_redirect_uri_is_refused(control, client):
    _, challenge = _verifier_and_challenge()
    with pytest.raises(oauth.OAuthError):
        _authorize(control, client, challenge, redirect="https://evil.example.com/steal")


@pytest.mark.parametrize(
    "redirect",
    [
        REDIRECT + "/..",
        REDIRECT + "?x=1",
        REDIRECT + "#frag",
        REDIRECT.replace("https", "http"),
        REDIRECT.rstrip("t"),
        REDIRECT + "@evil.example.com",
        REDIRECT.upper(),
    ],
)
def test_redirect_uri_matching_is_exact(control, client, redirect):
    """Prefix, suffix, or case-insensitive matching all become open redirects."""
    _, challenge = _verifier_and_challenge()
    with pytest.raises(oauth.OAuthError):
        _authorize(control, client, challenge, redirect=redirect)


def test_registration_refuses_a_non_https_redirect(control):
    with pytest.raises(oauth.OAuthError):
        oauth.register_client(
            control, client_name="x", redirect_uris=["http://evil.example.com/cb"], now_ms=NOW
        )


def test_registration_requires_at_least_one_redirect(control):
    with pytest.raises(oauth.OAuthError):
        oauth.register_client(control, client_name="x", redirect_uris=[], now_ms=NOW)


# =============================================================================
# Goal: read a token straight out of the database.
# =============================================================================
def test_no_token_is_stored_in_recoverable_form(control, client):
    """The control database is encrypted, but a token readable there is a token
    that leaks in any backup, any dump, any `doctor` output someone pastes."""
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    granted = oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )
    blob = "\n".join(str(dict(r)) for r in control.execute("SELECT * FROM oauth_tokens").fetchall())
    assert granted["access_token"] not in blob
    assert granted["refresh_token"] not in blob
    # the code, too — it is a bearer credential until it is consumed
    codes = "\n".join(str(dict(r)) for r in control.execute("SELECT * FROM oauth_codes").fetchall())
    assert code not in codes


def test_tokens_carry_real_entropy(control, client):
    seen = set()
    for _ in range(20):
        verifier, challenge = _verifier_and_challenge()
        code = _approved_code(control, client, challenge)
        granted = oauth.exchange_code(
            control,
            code=code,
            client_id=client["client_id"],
            redirect_uri=REDIRECT,
            code_verifier=verifier,
            now_ms=NOW,
        )
        assert len(granted["access_token"]) >= 32
        seen.add(granted["access_token"])
    assert len(seen) == 20


# =============================================================================
# Goal: keep using a token after it should have stopped working.
# =============================================================================
def test_an_expired_access_token_is_rejected(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    granted = oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )
    token = granted["access_token"]
    assert oauth.validate_access_token(control, token, now_ms=NOW) is not None
    assert oauth.validate_access_token(control, token, now_ms=NOW + oauth.ACCESS_TTL_MS + 1) is None


def test_revocation_takes_effect_immediately(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    granted = oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )
    assert oauth.revoke_all(control, now_ms=NOW) >= 1
    assert oauth.validate_access_token(control, granted["access_token"], now_ms=NOW) is None
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(
            control,
            refresh_token=granted["refresh_token"],
            client_id=client["client_id"],
            now_ms=NOW,
        )


def test_a_refresh_token_is_rotated_and_the_old_one_dies(control, client):
    """Reuse of a rotated refresh token is the signal that it was stolen."""
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    granted = oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )
    again = oauth.refresh(
        control, refresh_token=granted["refresh_token"], client_id=client["client_id"], now_ms=NOW
    )
    assert again["refresh_token"] != granted["refresh_token"]
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(
            control,
            refresh_token=granted["refresh_token"],
            client_id=client["client_id"],
            now_ms=NOW,
        )


def test_a_refresh_token_cannot_be_used_as_an_access_token(control, client):
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    granted = oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )
    assert oauth.validate_access_token(control, granted["refresh_token"], now_ms=NOW) is None


def test_a_garbage_token_is_rejected_without_raising(control):
    for junk in ("", "   ", "Bearer", "a" * 500, "../../etc/passwd", None):
        assert oauth.validate_access_token(control, junk, now_ms=NOW) is None


# =============================================================================
# Goal: find a write verb behind the new surface.
# =============================================================================
def test_the_oauth_module_grants_no_send_authority(control, client):
    """Going public must not smuggle in authority. A granted scope may never
    imply anything but reading."""
    verifier, challenge = _verifier_and_challenge()
    code = _approved_code(control, client, challenge)
    granted = oauth.exchange_code(
        control,
        code=code,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_verifier=verifier,
        now_ms=NOW,
    )
    assert granted["scope"] == oauth.READ_ONLY_SCOPE
    claims = oauth.validate_access_token(control, granted["access_token"], now_ms=NOW)
    assert claims["scope"] == oauth.READ_ONLY_SCOPE
    assert "send" not in claims["scope"] and "write" not in claims["scope"]


def test_a_client_cannot_request_a_wider_scope(control, client):
    _, challenge = _verifier_and_challenge()
    pending = oauth.begin_authorization(
        control,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_challenge=challenge,
        code_challenge_method="S256",
        state="xyz",
        scope="send approve admin",
        now_ms=NOW,
    )
    oauth.approve(control, user_code=pending["user_code"], now_ms=NOW)
    got = oauth.poll(control, request_id=pending["request_id"], now_ms=NOW)
    assert got["scope"] == oauth.READ_ONLY_SCOPE
