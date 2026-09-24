"""Malformed-input and privacy probes at the HTTP boundary."""

import logging

from starlette.testclient import TestClient

from comms.transports.telegram.config import DemoConfig
from comms.transports.telegram.observability.logging import install_safe_logging
from comms.transports.telegram.server import create_app

install_safe_logging()

BASE = "http://127.0.0.1:8766"
HEADERS = {
    "Mcp-Protocol-Version": "2026-07-28",
    "Mcp-Method": "tools/call",
    "Mcp-Name": "telegram_status",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
BODY = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"telegram_status","arguments":{}}}'


def test_bad_version_is_sdk_protocol_error():
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post(
            "/mcp", headers={**HEADERS, "Mcp-Protocol-Version": "1999-01-01"}, content=BODY
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


def test_invalid_json_is_parse_error():
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post("/mcp", headers=HEADERS, content=b"not json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


def test_oversized_body_rejected_before_dispatch():
    big = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"telegram_status","arguments":{},"pad":"' + b"x" * 70000 + b'"}}'
    )
    assert len(big) > 65536
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post("/mcp", headers=HEADERS, content=big)
    assert response.status_code == 413


def test_framed_tool_failure_stays_tool_result():
    import json

    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    bad_args = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"telegram_list_chats","arguments":{"limit":9999},"_meta":'
        + json.dumps(meta).encode()
        + b"}}"
    )
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post(
            "/mcp", headers={**HEADERS, "Mcp-Name": "telegram_list_chats"}, content=bad_args
        )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True


def test_host_canary_never_in_response_or_logs(caplog):
    caplog.set_level(logging.WARNING)
    evil = "SYNTHETIC-CANARY-HOST.invalid"
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post("/mcp", headers={**HEADERS, "Host": evil}, content=BODY)
    assert response.status_code == 421
    assert "SYNTHETIC-CANARY-HOST" not in response.text
    assert "SYNTHETIC-CANARY-HOST" not in caplog.text


def test_origin_canary_never_in_response_or_logs(caplog):
    caplog.set_level(logging.WARNING)
    evil = "https://SYNTHETIC-CANARY-ORIGIN.invalid"
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post("/mcp", headers={**HEADERS, "Origin": evil}, content=BODY)
    assert "SYNTHETIC-CANARY-ORIGIN" not in response.text
    assert "SYNTHETIC-CANARY-ORIGIN" not in caplog.text


def test_duplicate_keys_rejected_at_protocol_level():
    import json

    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    dup = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"telegram_list_chats",'
        b'"arguments":{"project_ref":"tpr_' + b"a" * 26 + b'"},'
        b'"arguments":{"limit":9999},"_meta":' + json.dumps(meta).encode() + b"}}"
    )
    with TestClient(create_app(DemoConfig()), base_url=BASE) as client:
        response = client.post("/mcp", headers=HEADERS, content=dup)
    # Installed SDK last-wins duplicate keys, so the strict preflight rejects them.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700
