"""The authenticated ingress over real TCP (design §2.2, G10, G11)."""

import asyncio
import os
import socket

import httpx
import pytest
import uvicorn
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from comms.transports.telegram.consent.challenge import StubSigner
from comms.transports.telegram.http_guards import UNAUTHORIZED_BODY
from comms.transports.telegram.ipc.leases import mint_lease
from comms.transports.telegram.keys.store import provision_lease_seed, provision_missing
from comms.transports.telegram.runtime.composition import build_runtime
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

CLIENT = "tcl_" + "a" * 26
RUNTIME = b"\x05" * 16


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def ingress(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    keys = tmp_path / "keys"
    provision_missing(keys, phases=(2, 3))
    seed = provision_lease_seed(keys, CLIENT)
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    (tmp_path / "anchor").mkdir(mode=0o700)
    port = _free_port()
    services = build_runtime(
        conn,
        key_dir=keys,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        runtime_id=RUNTIME,
        agent_verify=StubSigner(seed=0x07).verify,
        port=port,
        limits={"telegram_status": 3},
    )
    server = uvicorn.Server(
        uvicorn.Config(services.ingress_app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield conn, services, port, seed
    server.should_exit = True
    await task


def _lease(seed, *, epoch=1, now=None, runtime=RUNTIME, client=CLIENT):
    import time

    return mint_lease(
        seed=seed, client=client, epoch=epoch, now=int(now or time.time()), runtime_id=runtime
    )


async def _call(port, token, name="telegram_status", raw=None):
    params = {
        "name": name,
        "arguments": {},
        "_meta": {PROTOCOL_VERSION_META_KEY: "2026-07-28", CLIENT_CAPABILITIES_META_KEY: {}},
    }
    headers = {
        "Mcp-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        if raw is not None:
            return await client.post(
                "/mcp", headers={**headers, "Content-Type": "application/json"}, content=raw
            )
        return await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        )


async def test_a_good_bearer_reaches_status(ingress):
    _conn, _services, port, seed = ingress
    response = await _call(port, _lease(seed))
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["ok"] is True


async def test_every_bad_bearer_gets_identical_bytes_before_parsing(ingress):
    _conn, _services, port, seed = ingress
    import time

    bad = {
        "missing": None,
        "garbage": "not-a-lease",
        "expired": _lease(seed, now=time.time() - 3600),
        "wrong_epoch": _lease(seed, epoch=2),
        "wrong_runtime": _lease(seed, runtime=b"\x06" * 16),
        "unknown_client": _lease(os.urandom(32), client="tcl_" + "z" * 26),
    }
    for label, token in bad.items():
        response = await _call(port, token, raw=b'{"not": "even", "not": "json-rpc"}')
        assert (response.status_code, response.content) == (401, UNAUTHORIZED_BODY), label


async def test_disabled_client_is_refused_inside_lease_lifetime(ingress):
    conn, _services, port, seed = ingress
    token = _lease(seed)
    assert (await _call(port, token)).status_code == 200
    conn.execute("UPDATE mcp_clients SET enabled = 0")
    conn.commit()
    response = await _call(port, token)
    assert (response.status_code, response.content) == (401, UNAUTHORIZED_BODY)


async def test_duplicate_keys_are_refused_after_auth(ingress):
    _conn, _services, port, seed = ingress
    response = await _call(
        port, _lease(seed), raw=b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"tools/call"}'
    )
    assert response.status_code == 400


async def test_an_oversize_body_is_refused_and_charges_nothing(ingress):
    conn, _services, port, seed = ingress
    response = await _call(port, _lease(seed), raw=b'{"pad":"' + b"x" * 70_000 + b'"}')
    assert response.status_code >= 400
    assert conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0] == 0


async def test_the_rate_limit_is_429_with_retry_after(ingress):
    _conn, _services, port, seed = ingress
    statuses = [(await _call(port, _lease(seed))).status_code for _ in range(4)]
    assert statuses[:3] == [200, 200, 200] and statuses[3] == 429
    response = await _call(port, _lease(seed))
    assert int(response.headers["retry-after"]) >= 1


async def test_sensitive_tools_without_a_project_answer_honestly(ingress):
    _conn, _services, port, seed = ingress
    response = await _call(port, _lease(seed), name="telegram_get_unread")
    body = response.json()["result"]["structuredContent"]
    assert (
        body["ok"] is False and body["error"]["code"] == "INVALID_ARGUMENT"
    )  # project_ref is required
