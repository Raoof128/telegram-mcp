"""Operational readiness for the MCP daemon: doctor checks + key provisioning.

The launchd unit exits 1 on a missing Keychain key and KeepAlive restarts it, so
an unprovisioned daemon presents as a restart loop rather than a clear error.
These make the precondition inspectable and fixable from the CLI.
"""

import os

import pytest

from whatsvault import doctor
from whatsvault.cli import commands
from whatsvault.crypto import keystore as KS
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import acl, audit, auth


@pytest.fixture
def dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    v.execute("INSERT INTO accounts(id,phone_number_id) VALUES('acc','pn')")
    v.execute("INSERT INTO conversations(id,account_id,type) VALUES('cnv','acc','dm')")
    v.commit()
    return v, c


def _by(findings):
    return {f["check"]: f for f in findings}


# ---- doctor.check_mcp ---------------------------------------------------------
def test_schema_checks_pass_on_a_migrated_vault(dbs):
    v, c = dbs
    f = _by(doctor.check_mcp(v, c))
    assert f["mcp_visibility_column"]["ok"] is True
    assert f["audit_log_present"]["ok"] is True
    assert f["audit_log_append_only"]["ok"] is True


def test_local_only_count_is_reported(dbs):
    v, c = dbs
    acl.set_visibility(v, "cnv", "LOCAL_ONLY")
    f = _by(doctor.check_mcp(v, c))
    assert "1" in f["mcp_local_only_conversations"]["detail"]
    assert f["mcp_local_only_conversations"]["ok"] is True  # informational, never a failure


def test_key_checks_are_skipped_without_a_keystore(dbs):
    """doctor must stay runnable in CI, where there is no Keychain."""
    v, c = dbs
    assert "mcp_token_provisioned" not in _by(doctor.check_mcp(v, c))


def test_key_checks_run_when_a_keystore_is_given(dbs):
    v, c = dbs
    ks = KS.MemoryKeyStore()
    f = _by(doctor.check_mcp(v, c, ks=ks))
    assert f["mcp_token_provisioned"]["ok"] is False
    assert f["mcp_audit_key_provisioned"]["ok"] is False
    auth.provision_token(ks)
    ks.provision(audit.AUDIT_KEY_NAME, 32)
    f = _by(doctor.check_mcp(v, c, ks=ks))
    assert f["mcp_token_provisioned"]["ok"] is True
    assert f["mcp_audit_key_provisioned"]["ok"] is True


def test_missing_audit_log_is_detected(dbs, tmp_path):
    v, _ = dbs
    bare = C.open_db(str(tmp_path / "bare.db"), os.urandom(32))  # never migrated
    f = _by(doctor.check_mcp(v, bare))
    assert f["audit_log_present"]["ok"] is False


# ---- the mcp-provision verb ---------------------------------------------------
def test_provision_creates_both_keys(dbs):
    v, c = dbs
    ks = KS.MemoryKeyStore()
    ctx = commands.Ctx(v, c, ks=ks)
    out = commands.cmd_mcp_provision(ctx, {"reveal": "1"})
    assert out["ok"] is True
    assert set(out["provisioned"]) == {auth.TOKEN_KEY_NAME, audit.AUDIT_KEY_NAME}
    assert ks.require(auth.TOKEN_KEY_NAME, 32)
    assert ks.require(audit.AUDIT_KEY_NAME, 32)


def test_provision_is_idempotent_and_never_rotates(dbs):
    """Rotating the token would silently break an already-configured connector,
    and rotating the audit key would orphan every existing audit HMAC."""
    v, c = dbs
    ks = KS.MemoryKeyStore()
    ctx = commands.Ctx(v, c, ks=ks)
    commands.cmd_mcp_provision(ctx, {"reveal": "1"})
    before = ks.require(auth.TOKEN_KEY_NAME, 32)
    out = commands.cmd_mcp_provision(ctx, {"reveal": "1"})
    assert out["provisioned"] == []
    assert out["already_present"]
    assert ks.require(auth.TOKEN_KEY_NAME, 32) == before


def test_token_is_withheld_unless_reveal_is_requested(dbs):
    """cli.main prints results to stdout, and the launchd units capture stdout to
    a log file. The token must not land there by accident."""
    v, c = dbs
    ctx = commands.Ctx(v, c, ks=KS.MemoryKeyStore())
    out = commands.cmd_mcp_provision(ctx, {})
    assert "token" not in out
    assert "reveal" in out["note"].lower()


def test_reveal_returns_the_real_token(dbs):
    v, c = dbs
    ks = KS.MemoryKeyStore()
    ctx = commands.Ctx(v, c, ks=ks)
    out = commands.cmd_mcp_provision(ctx, {"reveal": "1"})
    assert out["token"] == ks.require(auth.TOKEN_KEY_NAME, 32).hex()


def test_provision_verb_is_registered_and_not_forbidden():
    assert "mcp-provision" in commands.COMMANDS
    assert set(commands.COMMANDS).isdisjoint(commands.FORBIDDEN_VERBS)


def test_ctx_keystore_is_optional():
    """Existing call sites construct Ctx(vault, control) with no keystore."""
    ctx = commands.Ctx(None, None)
    assert ctx.ks is None


def test_provision_without_a_keystore_fails_cleanly(dbs):
    v, c = dbs
    out = commands.cmd_mcp_provision(commands.Ctx(v, c), {"reveal": "1"})
    assert out["ok"] is False and "keystore" in out["error"].lower()


def test_existing_key_of_wrong_length_is_reported_not_overwritten(dbs):
    """require() raises ValueError (not KeyMissing) for a wrong-sized key. Treating
    that as 'absent' would attempt a provision, hit KeyExists, and crash — and if it
    ever succeeded it would destroy a key rather than report a corrupt one."""
    v, c = dbs
    ks = KS.MemoryKeyStore()
    ks._d[auth.TOKEN_KEY_NAME] = b"too-short"
    out = commands.cmd_mcp_provision(commands.Ctx(v, c, ks=ks), {})
    assert out["ok"] is False
    assert auth.TOKEN_KEY_NAME in out["error"]
    assert ks._d[auth.TOKEN_KEY_NAME] == b"too-short"  # untouched


def test_provisioned_token_actually_authenticates_the_daemon(dbs):
    """The whole operator loop: provision -> reveal -> the daemon accepts it.
    Proves the CLI and the transport agree on the same Keychain key name.

    Booted over a real socket rather than driven in-process: the MCP session
    manager needs the ASGI lifespan running, so an authenticated request cannot
    be served without it (an in-process call raises "Task group is not
    initialized" instead of succeeding).
    """
    import json
    import socket
    import threading
    import time
    import urllib.error
    import urllib.request

    uvicorn = pytest.importorskip("uvicorn")
    from apps.mcp import server

    v, c = dbs
    ks = KS.MemoryKeyStore()
    out = commands.cmd_mcp_provision(commands.Ctx(v, c, ks=ks), {"reveal": "1"})
    token = out["token"]

    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    app = server.build_app(v, c, token, ks.require(audit.AUDIT_KEY_NAME, 32), port=port)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not start")

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        }
    ).encode()

    def status(tok):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            with e:  # HTTPError is a file-like response; closing avoids a leak
                return e.code

    try:
        assert status(None) == 401
        assert status("wrong-token") == 401
        assert status(token) == 200  # the provisioned token really works
    finally:
        srv.should_exit = True
