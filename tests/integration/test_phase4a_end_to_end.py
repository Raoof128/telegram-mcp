"""list_projects / resolve_project through the whole chain (design §2.8)."""

import asyncio
import socket
import time

import httpx
import pytest
import uvicorn
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from comms.transports.telegram.disclosure.receipts import verify_proof
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.ipc.leases import mint_lease
from comms.transports.telegram.keys.store import provision_lease_seed, provision_missing
from comms.transports.telegram.runtime.legacy_composition import build_runtime
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

CODEX = "tcl_" + "a" * 26
CLAUDE = "tcl_" + "c" * 26
RUNTIME = b"\x05" * 16


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
    port = _free_port()
    services = build_runtime(
        conn,
        key_dir=keys,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        runtime_id=RUNTIME,
        port=port,
    )
    admin = services.admin_router

    def create(slug, name):
        return admin.dispatch(
            {
                "cmd": "project create",
                "args": {"slug": slug, "display_name": name},
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
                    "project_ref": project,
                    "client_ref": client,
                    "egress_level": level,
                },
            }
        )
        assert granted["ok"], granted
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
        "ops": ops,
        "society": society,
        "admin": admin,
    }
    server.should_exit = True
    await serving


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
    assert "result" in response.json(), response.text
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
    assert d["proof_payload"]["schema"] == "tg-mcp-disclosure/v2"
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


async def test_same_client_concurrency_accounts_every_call(world):
    results = await asyncio.gather(
        *(call(world, CODEX, "telegram_list_projects", {}) for _ in range(3))
    )
    assert all(body["ok"] for body in results), results
    assert counts(world["conn"]) == (3, 3, 3)


async def test_without_a_session_telegram_tools_refuse_before_any_prompt(world):
    body = await call(
        world,
        CODEX,
        "telegram_get_context",
        {"project_ref": world["ops"], "message_ref": "tgm_" + "a" * 26},
    )
    assert body["error"]["code"] == "AUTH_REQUIRED"  # this world has no Telegram session
