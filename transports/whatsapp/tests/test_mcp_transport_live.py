"""End-to-end transport gate (#18/#19). The unit tests cover the middleware in
isolation; this boots the real ASGI app over a real socket and speaks MCP, so
'the transport has never been executed' cannot silently become true again."""

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from apps.mcp import server
from whatsvault.db import connection as C
from whatsvault.db import migrations as M

uvicorn = pytest.importorskip("uvicorn")

TOKEN = "live-transport-token"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    d = tmp_path_factory.mktemp("live")
    v = C.open_db(str(d / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(d / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    port = _free_port()
    app = server.build_app(v, c, TOKEN, os.urandom(32), port=port)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not start")
    yield port
    srv.should_exit = True


def _post(port, auth, host=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp", data=json.dumps(INIT).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Host", host or f"127.0.0.1:{port}")
    if auth:
        req.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        # HTTPError is itself a file-like response and leaks if left unclosed.
        with e:
            return e.code, e.read()


def test_anonymous_request_is_refused(live):
    assert _post(live, None)[0] == 401


def test_wrong_token_is_refused(live):
    assert _post(live, "Bearer wrong")[0] == 401


def test_wrong_scheme_is_refused(live):
    assert _post(live, f"Basic {TOKEN}")[0] == 401


def test_authenticated_initialize_succeeds(live):
    status, body = _post(live, f"Bearer {TOKEN}")
    assert status == 200
    assert b'"result"' in body and b'"tools"' in body


def test_forged_host_header_is_refused(live):
    """DNS-rebinding protection: loopback bind is not on its own a boundary."""
    assert _post(live, f"Bearer {TOKEN}", host="evil.example.com") == (421, b"Invalid Host header")
