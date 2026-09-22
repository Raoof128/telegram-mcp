"""Raw-wire acceptance against the installed SDK (modern 2026-07-28 path)."""

import mcp.types as wire_types
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from starlette.testclient import TestClient

from telegram_mcp.config import DemoConfig
from telegram_mcp.server import create_app

EXPECTED = [
    "telegram_status",
    "telegram_list_projects",
    "telegram_resolve_project",
    "telegram_list_chats",
    "telegram_resolve_peer",
    "telegram_get_messages",
    "telegram_get_context",
    "telegram_search_messages",
    "telegram_cross_project_search",
    "telegram_get_unread",
]


def modern_request(client, method, params):
    params = dict(params)
    params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: "2026-07-28",
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    headers = {
        "Mcp-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )


def test_modern_status_without_initialize():
    with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as client:
        response = modern_request(
            client, "tools/call", {"name": "telegram_status", "arguments": {}}
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers
    assert response.json()["result"]["structuredContent"]["data"]["connected"] is False


def test_discover_and_list_match_static_catalogue():
    with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as client:
        listed = modern_request(client, "tools/list", {}).json()["result"]["tools"]
    assert [t["name"] for t in listed] == EXPECTED
    for tool in listed:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["openWorldHint"] is False
        assert "securitySchemes" not in tool
    # SDK wire models accept the raw descriptors too.
    for tool in listed:
        wire_types.Tool.model_validate(tool)


def test_known_tool_domain_error_is_bounded():
    with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as client:
        response = modern_request(
            client, "tools/call", {"name": "telegram_list_chats", "arguments": {}}
        )
    body = response.json()["result"]
    assert body["isError"] is True
    assert body["structuredContent"]["error"]["code"] == "INVALID_ARGUMENT"


def test_unknown_tool_error_never_reflects_name():
    with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as client:
        response = modern_request(
            client, "tools/call", {"name": "SYNTHETIC_NOPE", "arguments": {"x": 1}}
        )
    body = response.json()["result"]
    assert body["isError"] is True
    assert "SYNTHETIC_NOPE" not in response.text


def test_private_no_store_cache_headers():
    with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as client:
        response = modern_request(
            client, "tools/call", {"name": "telegram_status", "arguments": {}}
        )
    assert response.headers["cache-control"] == "private, no-store"


def test_result_carries_sdk_metadata_tolerantly():
    with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as client:
        response = modern_request(
            client, "tools/call", {"name": "telegram_status", "arguments": {}}
        )
    envelope = response.json()["result"]
    assert envelope["structuredContent"]["ok"] is True
    # 2026 additions (serverInfo/resultType) are tolerated, never rejected.
    assert envelope["structuredContent"]["data"]["security_epoch"] == 1
