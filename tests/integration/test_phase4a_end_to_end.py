"""list_projects / resolve_project through the whole chain (design §2.8)."""

import asyncio
import base64
import hashlib
import socket
import time

import httpx
import pytest
import uvicorn
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from comms.transports.telegram.consent.challenge import StubSigner
from comms.transports.telegram.disclosure.receipts import verify_proof
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.ipc.framing import (
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)
from comms.transports.telegram.ipc.leases import mint_lease
from comms.transports.telegram.keys.store import provision_lease_seed, provision_missing
from comms.transports.telegram.runtime.composition import build_runtime
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

CODEX = "tcl_" + "a" * 26
CLAUDE = "tcl_" + "c" * 26
RUNTIME = b"\x05" * 16
PROOF = {"method": "stub"}


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Agent:
    """A signing agent on the real prompter. ``before_approve`` runs mid-prompt."""

    def __init__(self, signer, mode="approve"):
        self.signer, self.mode, self.prompts, self.before_approve = signer, mode, 0, None
        self.delay = 0.0

    async def run(self, reader, writer):
        while True:
            raw = await read_frame(reader, idle_s=60)
            if not raw:
                return
            frame = decode_json_frame(raw)
            self.prompts += 1
            if self.before_approve is not None:
                self.before_approve()
            if self.mode == "silent":
                continue
            if self.delay:
                await asyncio.sleep(self.delay)
            challenge = base64.urlsafe_b64decode(
                frame["challenge"] + "=" * (-len(frame["challenge"]) % 4)
            )
            envelope = {
                "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
                "sig": _b64url(self.signer.sign(challenge)),
                "key_id": self.signer.key_id,
            }
            await write_frame(
                writer,
                encode_json_frame(
                    {"type": "APPROVAL", "handle": frame["handle"], "envelope": envelope}
                ),
            )


@pytest.fixture
async def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    keys = tmp_path / "keys"
    provision_missing(keys, phases=(2, 3))
    seeds = {CODEX: provision_lease_seed(keys, CODEX), CLAUDE: provision_lease_seed(keys, CLAUDE)}
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    conn.execute("DELETE FROM projects")
    conn.execute(
        "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding, client_kind, created_at)"
        " VALUES (1, ?, 'bearer', 'binding-2', 'claude_code_local', 'now')",
        (CLAUDE,),
    )
    conn.commit()
    (tmp_path / "anchor").mkdir(mode=0o700)
    signer = StubSigner(seed=0x07)
    port = _free_port()
    services = build_runtime(
        conn,
        key_dir=keys,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        runtime_id=RUNTIME,
        agent_verify=signer.verify,
        port=port,
        presence_verifier=lambda proof: proof == PROOF,
    )
    admin = services.admin_router

    def create(slug, name):
        return admin.dispatch(
            {
                "cmd": "project create",
                "args": {"presence": PROOF, "slug": slug, "display_name": name},
            }
        )["data"]["project_ref"]

    ops, society = create("ops", "Ops"), create("persian", "انجمن فارسی")
    for client, project, level in (
        (CODEX, ops, "full_text"),
        (CODEX, society, "metadata_only"),
        (CLAUDE, society, "full_text"),
    ):
        granted = admin.dispatch(
            {
                "cmd": "project grant-client",
                "args": {
                    "presence": PROOF,
                    "project_ref": project,
                    "client_ref": client,
                    "egress_level": level,
                },
            }
        )
        assert granted["ok"], granted
    agent = Agent(signer)
    left, right = socket.socketpair()
    d_reader, d_writer = await asyncio.open_connection(sock=left)
    a_reader, a_writer = await asyncio.open_connection(sock=right)
    session = asyncio.create_task(services.prompter.attach(d_reader, d_writer))
    agent_task = asyncio.create_task(agent.run(a_reader, a_writer))
    server = uvicorn.Server(
        uvicorn.Config(services.ingress_app, host="127.0.0.1", port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield {
        "conn": conn,
        "services": services,
        "port": port,
        "seeds": seeds,
        "agent": agent,
        "ops": ops,
        "society": society,
        "admin": admin,
    }
    server.should_exit = True
    await serving
    agent_task.cancel()
    session.cancel()


async def call(world, client, name, arguments, *, runtime=RUNTIME):
    token = mint_lease(
        seed=world["seeds"][client],
        client=client,
        epoch=1,
        now=int(time.time()),
        runtime_id=runtime,
    )
    params = {
        "name": name,
        "arguments": arguments,
        "_meta": {PROTOCOL_VERSION_META_KEY: "2026-07-28", CLIENT_CAPABILITIES_META_KEY: {}},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Mcp-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{world['port']}", timeout=90
    ) as client_http:
        response = await client_http.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        )
    assert response.status_code == 200, response.text
    return response.json()["result"]["structuredContent"]


def counts(conn):
    return tuple(
        conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("disclosure_receipts", "exposure_ledger", "audit_events")
    )


async def test_list_projects_is_a_real_accounted_disclosure(world):
    body = await call(world, CODEX, "telegram_list_projects", {})
    assert body["ok"] is True
    listed = {p["project_ref"]: p["egress_level"] for p in body["data"]["projects"]}
    assert listed == {world["ops"]: "full_text", world["society"]: "metadata_only"}
    meta = body["meta"]
    assert meta["source"] == "gateway" and meta["next_cursor"] is None
    ref = meta["disclosure"]["receipt_ref"]
    assert counts(world["conn"]) == (1, 1, 1)  # one receipt, one global ledger row, one event
    assert verify_persisted_receipt(world["conn"], ref)
    public = (
        world["conn"]
        .execute(
            "SELECT public_key_b64url FROM verification_keys WHERE purpose = 'disclosure_proof'"
        )
        .fetchone()[0]
    )
    d = meta["disclosure"]
    assert verify_proof(
        d["proof_payload"],
        proof_signature=d["proof_signature"],
        proof_payload_sha256=d["proof_payload_sha256"],
        public_key_b64url=public,
    )
    assert world["agent"].prompts == 1
    assert (world["services"].coordinator._anchor_path).exists()


async def test_resolve_project_is_ambiguity_honest(world):
    body = await call(world, CODEX, "telegram_resolve_project", {"query": "انجمن"})
    assert body["ok"] is True
    assert [m["match_kind"] for m in body["data"]["matches"]] == ["prefix_display_name"]
    assert body["data"]["ambiguous"] is False


async def test_concurrent_bearers_resolve_to_their_own_principal(world):
    codex, claude = await asyncio.gather(
        call(world, CODEX, "telegram_list_projects", {}),
        call(world, CLAUDE, "telegram_list_projects", {}),
    )
    assert {p["project_ref"] for p in codex["data"]["projects"]} == {world["ops"], world["society"]}
    assert {p["project_ref"] for p in claude["data"]["projects"]} == {world["society"]}
    assert world["agent"].prompts == 2


async def test_same_client_concurrency_prompts_once_per_call(world):
    results = await asyncio.gather(
        *(call(world, CODEX, "telegram_list_projects", {}) for _ in range(3))
    )
    assert all(body["ok"] for body in results), results
    assert world["agent"].prompts == 3  # no re-prompt: the snapshot never moved under a call
    assert counts(world["conn"]) == (3, 3, 3)


async def test_grant_revoked_during_prompt_refuses(world):
    def revoke():
        world["admin"].dispatch(
            {
                "cmd": "project revoke-client",
                "args": {"presence": PROOF, "project_ref": world["ops"], "client_ref": CODEX},
            }
        )
        world["agent"].before_approve = None

    world["agent"].before_approve = revoke
    body = await call(world, CODEX, "telegram_list_projects", {})
    assert body["ok"] is False and body["error"]["code"] == "POLICY_CHANGED"
    assert counts(world["conn"]) == (0, 0, 0)


async def test_consent_timeout_is_denied_and_charges_nothing(world, monkeypatch):
    world["agent"].mode = "silent"
    consent = world["services"].coordinator._consent
    monkeypatch.setattr(consent, "_wait_s", 0.3)
    body = await call(world, CODEX, "telegram_list_projects", {})
    assert body["error"]["code"] == "CONSENT_DENIED"
    assert counts(world["conn"]) == (0, 0, 0)
    assert world["services"].broker.pending_count() == 0


async def test_no_agent_is_unavailable_at_once(world):
    world["services"].prompter._drop()
    started = time.monotonic()
    body = await call(world, CODEX, "telegram_list_projects", {})
    assert body["error"]["code"] == "CONSENT_UNAVAILABLE"
    assert time.monotonic() - started < 5


async def test_cancellation_during_consent_never_reaches_retrieval(world):
    from comms.transports.telegram.runtime.identity import resolve_principal

    world["agent"].mode = "silent"
    services = world["services"]
    task = asyncio.create_task(
        services.coordinator.disclose(
            tool_name="telegram_list_projects",
            arguments={"limit": 20},
            adapter=_Exploding(),
            principal=resolve_principal(world["conn"], CODEX),
        )
    )
    while world["agent"].prompts == 0:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert counts(world["conn"]) == (0, 0, 0)
    assert services.broker.pending_count() == 0
    assert services.coordinator._ledger._reservations == {}


class _Exploding:
    async def retrieve(self, **_kwargs):
        raise AssertionError("retrieval ran after a cancelled consent")


async def test_without_a_session_telegram_tools_refuse_before_any_prompt(world):
    body = await call(
        world,
        CODEX,
        "telegram_get_context",
        {"project_ref": world["ops"], "message_ref": "tgm_" + "a" * 26},
    )
    assert body["error"]["code"] == "AUTH_REQUIRED"  # this world has no Telegram session
    assert world["agent"].prompts == 0


async def test_a_client_that_disconnects_mid_prompt_invalidates_its_challenge(world):
    # Spec §9.8: if the originating MCP request is cancelled/disconnected before
    # consent is consumed, the challenge MUST be invalidated.
    world["agent"].delay = 1.5
    token = mint_lease(
        seed=world["seeds"][CODEX], client=CODEX, epoch=1, now=int(time.time()), runtime_id=RUNTIME
    )
    params = {
        "name": "telegram_list_projects",
        "arguments": {},
        "_meta": {PROTOCOL_VERSION_META_KEY: "2026-07-28", CLIENT_CAPABILITIES_META_KEY: {}},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Mcp-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "telegram_list_projects",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{world['port']}", timeout=0.5) as http:
        with pytest.raises(httpx.TimeoutException):
            await http.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
            )
    await asyncio.sleep(0.3)
    assert world["services"].broker.pending_count() == 0, "challenge outlived its request"
    await asyncio.sleep(2.0)  # the operator's approval lands after the client left
    assert counts(world["conn"]) == (0, 0, 0)
