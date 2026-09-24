"""Lifecycle isolation and handshake-era compatibility (official SDK paths)."""

import json

from starlette.testclient import TestClient

from comms.transports.telegram.config import DemoConfig
from comms.transports.telegram.server import create_app

BASE = "http://127.0.0.1:8766"
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}
LEGACY_VERSION = "2025-11-25"
LEGACY_META = {
    "io.modelcontextprotocol/protocolVersion": LEGACY_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _headers(version, method, name=None):
    headers = {
        "Mcp-Protocol-Version": version,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _body(method, params, meta):
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}}
    ).encode()


def test_two_instances_are_independent():
    first, second = create_app(DemoConfig()), create_app(DemoConfig())
    with TestClient(first, base_url=BASE) as client:
        bad = client.post(
            "/mcp",
            headers=_headers("2026-07-28", "tools/call", "telegram_status"),
            content=b"not json",
        )
        assert bad.status_code == 400
    # Malformed traffic on the first instance leaves the second unaffected.
    with TestClient(second, base_url=BASE) as client:
        good = client.post(
            "/mcp",
            headers=_headers("2026-07-28", "tools/call", "telegram_status"),
            content=_body("tools/call", {"name": "telegram_status", "arguments": {}}, MODERN_META),
        )
    assert good.json()["result"]["structuredContent"]["data"]["connected"] is False


def test_legacy_handshake_era_catalogue_matches():
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        modern = client.post(
            "/mcp",
            headers=_headers("2026-07-28", "tools/list"),
            content=_body("tools/list", {}, MODERN_META),
        ).json()["result"]["tools"]
        legacy = client.post(
            "/mcp",
            headers=_headers(LEGACY_VERSION, "tools/list"),
            content=_body("tools/list", {}, LEGACY_META),
        ).json()["result"]["tools"]
    assert [t["name"] for t in legacy] == [t["name"] for t in modern]
    assert len(legacy) == 10


def test_legacy_call_succeeds_for_status():
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post(
            "/mcp",
            headers=_headers(LEGACY_VERSION, "tools/call", "telegram_status"),
            content=_body("tools/call", {"name": "telegram_status", "arguments": {}}, LEGACY_META),
        )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["data"]["connected"] is False
