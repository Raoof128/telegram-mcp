"""CLI verbs for the out-of-band OAuth approval.

The consent page deliberately has nothing to submit, so these verbs are the only
way an authorization is ever granted. If they do not exist, the public flow can
never complete — and if they grant anything beyond approving a pending read
request, the whole point of moving approval off the web page is lost.
"""

import os

import pytest

from whatsvault.cli import commands
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import oauth

NOW = 1_800_000_000_000
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


@pytest.fixture
def ctx(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return commands.Ctx(v, c)


# RFC 7636 appendix B: this verifier hashes to this challenge.
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def _pending(ctx, want_client=False):
    client = oauth.register_client(ctx.control, client_name="ChatGPT", redirect_uris=[REDIRECT], now_ms=NOW)
    started = oauth.begin_authorization(
        ctx.control,
        client_id=client["client_id"],
        redirect_uri=REDIRECT,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        state="xyz",
        scope=None,
        now_ms=NOW,
    )
    return (started, client["client_id"]) if want_client else started


def test_pending_lists_the_request_awaiting_approval(ctx):
    p = _pending(ctx)
    out = commands.cmd_oauth_pending(ctx, {})
    assert out["ok"] is True
    assert [r["user_code"] for r in out["pending"]] == [p["user_code"]]
    assert out["pending"][0]["client_name"] == "ChatGPT"


def test_approve_grants_the_named_request(ctx):
    p = _pending(ctx)
    out = commands.cmd_oauth_approve(ctx, {"code": p["user_code"]})
    assert out["ok"] is True
    assert oauth.poll(ctx.control, request_id=p["request_id"], now_ms=NOW) is not None


def test_approve_is_case_and_space_insensitive(ctx):
    """It is typed by a human off a screen."""
    p = _pending(ctx)
    typed = f"  {p['user_code'].lower()} "
    assert commands.cmd_oauth_approve(ctx, {"code": typed})["ok"] is True


def test_approve_requires_a_code(ctx):
    out = commands.cmd_oauth_approve(ctx, {})
    assert out["ok"] is False and "--code" in out["error"]


def test_approve_reports_an_unknown_code_without_crashing(ctx):
    out = commands.cmd_oauth_approve(ctx, {"code": "ZZZZZ-ZZZZZ"})
    assert out["ok"] is False and "error" in out


def test_approving_the_same_code_twice_is_refused(ctx):
    p = _pending(ctx)
    assert commands.cmd_oauth_approve(ctx, {"code": p["user_code"]})["ok"] is True
    assert commands.cmd_oauth_approve(ctx, {"code": p["user_code"]})["ok"] is False


def test_revoke_kills_every_live_token(ctx):
    p, client_id = _pending(ctx, want_client=True)
    commands.cmd_oauth_approve(ctx, {"code": p["user_code"]})
    got = oauth.poll(ctx.control, request_id=p["request_id"], now_ms=NOW)
    granted = oauth.exchange_code(
        ctx.control,
        code=got["code"],
        client_id=client_id,
        redirect_uri=REDIRECT,
        code_verifier=VERIFIER,
        now_ms=NOW,
    )
    out = commands.cmd_oauth_revoke(ctx, {})
    assert out["ok"] is True and out["revoked"] >= 1
    assert oauth.validate_access_token(ctx.control, granted["access_token"], now_ms=NOW) is None


def test_the_oauth_verbs_grant_no_send_authority(ctx):
    """Approving a read request must never become an approval verb in the sense
    this project forbids: nothing here touches the sender or a capability grant."""
    import inspect

    for name in ("cmd_oauth_approve", "cmd_oauth_pending", "cmd_oauth_revoke"):
        src = inspect.getsource(getattr(commands, name))
        for forbidden in ("sender", "capability", "approvals", "send_attempts", "nonce"):
            assert forbidden not in src, f"{name} touches {forbidden}"


def test_the_verbs_are_registered_and_not_forbidden():
    for verb in ("oauth-pending", "oauth-approve", "oauth-revoke"):
        assert verb in commands.COMMANDS
    assert set(commands.COMMANDS).isdisjoint(commands.FORBIDDEN_VERBS)
