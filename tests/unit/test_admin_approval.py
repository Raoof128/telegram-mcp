"""Live admin approvals (design §3.8): the wire is unchanged, secrets stay out."""

import asyncio
import base64
import hashlib
import json
import socket

from comms.transports.telegram.consent.admin_approval import AdminApprover, sentinel_ref
from comms.transports.telegram.consent.broker import ConsentBroker
from comms.transports.telegram.consent.challenge import StubSigner
from comms.transports.telegram.consent.prompter import Prompter
from comms.transports.telegram.ipc.admin import AdminRouter
from comms.transports.telegram.ipc.framing import (
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

KEY = b"\x07" * 32


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def _world(tmp_path, *, approve=True):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    stub = StubSigner(seed=0x07)
    broker = ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16
    )
    prompter = Prompter()
    left, right = socket.socketpair()
    dr, dw = await asyncio.open_connection(sock=left)
    ar, aw = await asyncio.open_connection(sock=right)
    session = asyncio.create_task(prompter.attach(dr, dw))
    await asyncio.sleep(0)
    seen = []

    async def agent():
        while True:
            raw = await read_frame(ar)
            if not raw:
                return
            frame = decode_json_frame(raw)
            challenge = base64.urlsafe_b64decode(
                frame["challenge"] + "=" * (-len(frame["challenge"]) % 4)
            )
            seen.append((json.loads(challenge), frame["display"]))
            if approve:
                env = {
                    "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
                    "sig": _b64url(stub.sign(challenge)),
                    "key_id": stub.key_id,
                }
                await write_frame(
                    aw,
                    encode_json_frame(
                        {"type": "APPROVAL", "handle": frame["handle"], "envelope": env}
                    ),
                )
            else:
                await write_frame(
                    aw,
                    encode_json_frame(
                        {"type": "DENIAL", "handle": frame["handle"], "reason": "USER-CANCEL"}
                    ),
                )

    helper = asyncio.create_task(agent())
    approver = AdminApprover(broker, prompter, privacy_key=KEY, conn=conn, wait_s=5)
    return approver, seen, (session, helper)


def test_sentinels_are_stable_well_formed_and_distinct():
    a = sentinel_ref(KEY, "operator", "tcl_")
    assert a == sentinel_ref(KEY, "operator", "tcl_")
    assert a.startswith("tcl_") and len(a) == 30 and a[4:].isalnum() and a[4:].islower()
    assert sentinel_ref(KEY, "pre-login", "tga_") != sentinel_ref(KEY, "operator", "tga_")


async def test_an_approval_yields_a_one_time_token_and_keeps_secrets_out(tmp_path):
    approver, seen, tasks = await _world(tmp_path)
    token = await approver.approve(
        "auth login", {"step": "code", "code": "22222", "phone": "9996621234"}
    )
    assert token is not None
    signed, display = seen[0]
    assert signed["tool"] == "admin.auth_login" and signed["client"] == sentinel_ref(
        KEY, "operator", "tcl_"
    )
    blob = json.dumps([signed, display])
    assert "22222" not in blob and "9996621234" not in blob
    args = {"step": "code", "code": "22222", "phone": "9996621234"}
    assert approver.verify({"token": token}, "auth login", args) is True
    assert approver.verify({"token": token}, "auth login", args) is False  # exactly once
    for t in tasks:
        t.cancel()


async def test_a_token_is_bound_to_the_exact_request_including_secrets(tmp_path):
    approver, seen, tasks = await _world(tmp_path)
    args = {"step": "code", "code": "11111", "phone": "9996621234"}
    token = await approver.approve("auth login", args)
    other = await approver.approve("auth login", {**args, "code": "22222"})
    assert (
        seen[0][0]["canonical_request_hmac"] != seen[1][0]["canonical_request_hmac"]
    )  # secrets bind, hidden
    assert approver.verify({"token": token}, "auth login", {**args, "code": "22222"}) is False
    assert approver.verify({"token": token}, "auth login", args) is False  # consumed by the miss
    assert approver.verify({"token": other}, "project rename", {"code": "22222"}) is False
    for t in tasks:
        t.cancel()


async def test_a_token_expires(tmp_path):
    approver, _seen, tasks = await _world(tmp_path)
    now = [0.0]
    approver._clock = lambda: now[0]
    token = await approver.approve("project create", {"slug": "ops"})
    now[0] = 61.0
    assert approver.verify({"token": token}, "project create", {"slug": "ops"}) is False
    for t in tasks:
        t.cancel()


async def test_a_denial_yields_no_token(tmp_path):
    approver, _seen, tasks = await _world(tmp_path, approve=False)
    assert await approver.approve("project create", {"slug": "ops"}) is None
    assert approver.verify({"token": "forged"}, "project create", {"slug": "ops"}) is False
    for t in tasks:
        t.cancel()


async def test_serve_admin_path_gates_through_the_approver(tmp_path):
    approver, _seen, tasks = await _world(tmp_path)
    calls = []

    async def handler(args):
        calls.append(args)
        return {"done": True}

    router = AdminRouter({"project create": handler}, request_verifier=approver.verify)
    token = await approver.approve("project create", {"slug": "ops"})
    swapped = await router.adispatch(
        {"cmd": "project create", "args": {"slug": "evil", "presence": {"token": token}}}
    )
    assert swapped["code"] == "PRESENCE_REQUIRED"  # a token cannot carry a different request
    token = await approver.approve("project create", {"slug": "ops"})
    ok = await router.adispatch(
        {"cmd": "project create", "args": {"slug": "ops", "presence": {"token": token}}}
    )
    assert ok == {"ok": True, "data": {"done": True}}
    replay = await router.adispatch(
        {"cmd": "project create", "args": {"slug": "ops", "presence": {"token": token}}}
    )
    assert replay["code"] == "PRESENCE_REQUIRED" and len(calls) == 1
    for t in tasks:
        t.cancel()


async def test_an_unrouted_gated_command_is_refused_without_a_prompt(tmp_path):
    approver, seen, tasks = await _world(tmp_path)
    router = AdminRouter({}, presence_verifier=approver.verify)
    response = await router.adispatch({"cmd": "auth revoke-this-session", "args": {}})
    assert response["code"] == "NOT_AVAILABLE_IN_PHASE"
    assert router.has_handler("auth revoke-this-session") is False
    assert seen == []  # the agent was never asked
    for task in tasks:
        task.cancel()


async def test_the_prompt_shows_the_summary_not_just_the_command(tmp_path):
    approver, seen, tasks = await _world(tmp_path)
    await approver.approve("project disable", {"project_ref": "tpr_" + "q" * 26})
    assert seen[0][1]["action_display"] == "project disable · project tpr_qqqq…qqqq"
    for t in tasks:
        t.cancel()
