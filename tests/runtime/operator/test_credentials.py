"""D39-PRE Task E8a: credential set|rotate|revoke — proved live, activated atomically, reloaded.

A failed proof keeps the working credential and destroys the candidate; the value never appears
in a reply, an audit row or a log (Review Focus 5).
"""

import json
import logging

import httpx
import pytest

from comms.core.credentials import active_credential
from comms.runtime.operator import OperatorContext, operator_handler
from comms.runtime.proofs import build_proofs

TOKEN = "7000000001:AAcanaryTOKENcanaryTOKENcanary12345"  # the Bot API canary shape
OTHER = "7000000002:AAotherTOKENotherTOKENotherTOKEN123"


def _bot(ok):
    def handler(request):
        body = (
            {"ok": True, "result": {"id": 7000000001, "is_bot": True}}
            if ok
            else {"ok": False, "error_code": 401}
        )
        return httpx.Response(200 if ok else 401, json=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def world(daemon_world):
    reloads = []
    ctx = daemon_world["ctx"]

    def make(ok=True):
        full = OperatorContext(
            writer=ctx.writer, store=ctx.store, clock=ctx.clock, legacy=ctx.legacy,
            secrets=daemon_world["state"].secrets,
            proofs=build_proofs(phone_number_id=None, transport=_bot(ok)),
            reload=lambda: reloads.append(1) or {"reloaded": True},
        )  # fmt: skip
        handle = operator_handler(full)
        return lambda *words, **args: handle({"command": list(words), **args})

    daemon_world["run"]("cutover", "run")
    return {**daemon_world, "make": make, "reloads": reloads}


def _leaks(world, value, caplog, reply):
    rows = world["state"].conn.execute("SELECT payload FROM audit_events").fetchall()
    return [where for where, text in (("reply", json.dumps(reply)), ("audit", json.dumps(rows)),
                                      ("log", caplog.text)) if value in text]  # fmt: skip


def test_a_proved_token_activates_and_the_adapters_reload(world, caplog):
    run = world["make"](ok=True)
    with caplog.at_level(logging.DEBUG):
        reply = run("credential", "set", purpose="telegram-bot-token", value=TOKEN)
    assert reply == {"purpose": "telegram-bot-token", "version": 1, "reloaded": True}
    conn, secrets = world["state"].conn, world["state"].secrets
    assert active_credential(conn, secrets, "telegram-bot-token") == TOKEN.encode()
    assert world["reloads"] == [1]
    assert _leaks(world, TOKEN, caplog, reply) == []


def test_a_failed_proof_keeps_the_working_credential_and_leaks_nothing(world, caplog):
    world["make"](ok=True)("credential", "set", purpose="telegram-bot-token", value=TOKEN)
    run = world["make"](ok=False)
    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as refused:
        run("credential", "rotate", purpose="telegram-bot-token", value=OTHER)
    assert OTHER not in str(refused.value)
    conn, secrets = world["state"].conn, world["state"].secrets
    assert active_credential(conn, secrets, "telegram-bot-token") == TOKEN.encode()
    assert secrets.versions("telegram-bot-token") == [1]  # the candidate was destroyed
    assert _leaks(world, OTHER, caplog, {}) == []


def test_the_webhook_secrets_are_checked_locally(world):
    run = world["make"]()
    with pytest.raises(ValueError, match="32 lowercase hex"):
        run("credential", "set", purpose="meta-app-secret", value="not-hex")
    assert (
        run("credential", "set", purpose="meta-app-secret", value="0123456789abcdef" * 2)["version"]
        == 1
    )
    with pytest.raises(ValueError, match="32 to 128 printable"):
        run("credential", "set", purpose="meta-webhook-secret", value="short")


def test_the_access_token_needs_the_phone_number_id(world):
    with pytest.raises(ValueError, match="phone_number_id"):
        world["make"]()("credential", "set", purpose="meta-access-token", value="EAAtoken")


def test_revoke_removes_the_credential_and_reloads(world):
    run = world["make"]()
    run("credential", "set", purpose="telegram-bot-token", value=TOKEN)
    assert run("credential", "revoke", purpose="telegram-bot-token") == {
        "purpose": "telegram-bot-token", "revoked": True, "reloaded": True}  # fmt: skip
    conn, secrets = world["state"].conn, world["state"].secrets
    assert active_credential(conn, secrets, "telegram-bot-token") is None


def test_the_session_and_unknown_purposes_are_refused(world):
    run = world["make"]()
    with pytest.raises(ValueError, match="transport telegram login"):
        run("credential", "set", purpose="telegram-session", value="x")
    with pytest.raises(ValueError, match="not a provider credential"):
        run("credential", "set", purpose="cursor-key", value="x")


def test_the_daemon_registers_session_revoke_only_with_the_comms_writer(daemon_world):
    from comms.transports.telegram.runtime.composition import admin_handlers

    paths, legacy = daemon_world["paths"], daemon_world["legacy"]
    session = object()  # never called: registration only
    common = {"key_dir": paths.legacy_keys, "anchor_path": paths.legacy_anchor, "telegram": session}
    assert "auth revoke-this-session" not in admin_handlers(legacy, **common)
    wired = admin_handlers(legacy, **common, audit_writer=daemon_world["state"].writer)
    assert {"auth login", "auth status", "auth revoke-this-session"} <= set(wired)
