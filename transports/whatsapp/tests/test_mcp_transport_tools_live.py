"""A tool call over the real transport must reach the database.

test_mcp_transport_live.py boots a real socket, but every assertion stops at
`initialize`. That covers the auth gate and the ASGI lifespan and nothing past
them — so it never executed a handler that touches SQLCipher.

Running the shipped server and calling `list_chats` for real produced:

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

The connections are opened once in main() on the main thread; the MCP SDK runs
synchronous tool handlers on a worker thread. Every database-backed tool failed
on the live server while 392 tests stayed green, because in-process tests call
the handlers on the thread that opened the connection.

These tests speak MCP over a socket, from a different thread than the one that
opened the databases, and assert on the tool's actual result.
"""

import concurrent.futures
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
from whatsvault.search import index as IDX

uvicorn = pytest.importorskip("uvicorn")

TOKEN = "live-tools-token"
PROTOCOL = "2025-06-18"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(vault):
    vault.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    vault.execute("INSERT INTO conversations(id, account_id, type, subject) VALUES('cnv','acc','dm','Alice')")
    vault.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES('msg_1','acc','cnv','in',1,60001,'min','text','hello there','manual_export',0)"
    )
    IDX.index_message(vault, "msg_1", "hello there")  # search reads the FTS index, not messages
    vault.commit()


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """Opened HERE, on the main thread, with the same flag main() uses. The
    handlers then run on a worker thread, which is the arrangement that broke."""
    d = tmp_path_factory.mktemp("livetools")
    v = C.open_db(str(d / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    _seed(v)
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
    yield port, c
    srv.should_exit = True


class _Client:
    """Minimal Streamable-HTTP MCP client: initialize, then call tools."""

    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.session = None
        self._n = 0

    def _rpc(self, method, params=None, notify=False):
        self._n += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._n
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("MCP-Protocol-Version", PROTOCOL)
        if self.session:
            req.add_header("Mcp-Session-Id", self.session)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                if not self.session:
                    self.session = r.headers.get("Mcp-Session-Id")
                return self._parse(r.read())
        except urllib.error.HTTPError as e:
            with e:  # HTTPError is a file-like response and leaks if left open
                raise AssertionError(f"{method} -> HTTP {e.code}: {e.read()[:300]!r}") from None

    @staticmethod
    def _parse(raw):
        """The transport may answer as JSON or as a one-event SSE stream."""
        text = raw.decode()
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return json.loads(text) if text.strip() else None

    def initialize(self):
        out = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        )
        self._rpc("notifications/initialized", notify=True)
        return out

    def call(self, name, arguments=None):
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})


@pytest.fixture
def client(live):
    port, _ = live
    c = _Client(port)
    c.initialize()
    return c


def _content(result):
    """The blocks of a successful tools/call, or a readable assertion failure."""
    assert "error" not in result, result["error"]
    body = result["result"]
    assert body.get("isError") is not True, body
    return body["content"]


def _rows(result):
    """A list-returning tool: the SDK emits one content block per element."""
    return [json.loads(b["text"]) for b in _content(result)]


def _payload(result):
    """A dict-returning tool: one block holding the whole value."""
    blocks = _content(result)
    assert len(blocks) == 1, blocks
    return json.loads(blocks[0]["text"])


# ---- the defect this file exists for -------------------------------------------
def test_a_database_backed_tool_succeeds_over_the_wire(client):
    """The whole point of the server. This failed on the shipped binary with a
    cross-thread ProgrammingError while every in-process test passed."""
    chats = _rows(client.call("list_chats"))
    assert [c["conversation_id"] for c in chats] == ["cnv"]


def test_search_returns_content_over_the_wire(client):
    hits = _rows(client.call("search", {"q": "hello"}))
    assert len(hits) == 1
    assert hits[0]["message_id"] == "msg_1"


def test_get_messages_returns_content_over_the_wire(client):
    msgs = _rows(client.call("get_messages", {"conversation_id": "cnv"}))
    assert [m["message_id"] for m in msgs] == ["msg_1"]


def test_every_registered_tool_is_callable_over_the_wire(client):
    """Not one representative tool: a handler that binds a connection at import
    time would leave exactly one tool broken, and a single-tool test would miss
    it. Nothing may raise the cross-thread error."""
    args = {
        "get_messages": {"conversation_id": "cnv"},
        "search": {"q": "hello"},
        "get_conversation_window": {"conversation_id": "cnv"},
        "get_message_status": {"message_id": "msg_1"},
    }
    for name in sorted(server.REGISTERED_TOOLS):
        result = client.call(name, args.get(name, {}))
        body = result.get("result", {})
        text = json.dumps(body)
        assert "thread" not in text.lower(), f"{name}: {text[:300]}"
        assert body.get("isError") is not True, f"{name}: {text[:300]}"


def test_concurrent_tool_calls_do_not_corrupt_the_connection(live):
    """Two assistants, or one that pipelines, put two calls on the connection at
    once. Serialising is fine; interleaving on a single SQLite connection is not."""
    port, _ = live
    clients = [_Client(port) for _ in range(4)]
    for c in clients:
        c.initialize()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: c.call("search", {"q": "hello"}), clients))
    for r in results:
        assert len(_rows(r)) == 1


def test_the_audit_log_is_written_from_the_worker_thread(live, client):
    """The audit record is written to control.db inside the same handler, so it
    hits the identical cross-thread hazard — and a silent failure there would
    lose the record of the call while the call itself succeeded."""
    _, control = live
    before = control.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    client.call("list_chats")
    after = control.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert after == before + 1


# ---- the arrangement is checked at startup, not per request ---------------------
def test_build_app_refuses_a_thread_pinned_connection(tmp_path):
    """The wrong flag must stop the daemon with the fix in the message, rather
    than serving happily and failing every tool call with an opaque 500."""
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))  # default: pinned
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    with pytest.raises(RuntimeError, match="check_same_thread=False"):
        server.build_app(v, c, TOKEN, os.urandom(32))


def test_the_refusal_names_the_offending_connection(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))  # control is the pinned one
    M.migrate(c, "control")
    with pytest.raises(RuntimeError, match=r"^control connection"):
        server.build_app(v, c, TOKEN, os.urandom(32))


def test_the_daemon_opens_for_threaded_serving(tmp_path, monkeypatch):
    """main() is pragma-no-cover, so assert the open path it uses actually yields
    connections that survive a worker thread."""
    from whatsvault.crypto import keystore as KS
    from whatsvault.ops import daemon, paths

    p = paths.Paths(str(tmp_path / "home"))
    monkeypatch.setenv("WHATSVAULT_HOME", p.home)
    ks = KS.MemoryKeyStore()
    from whatsvault.ops import bootstrap

    bootstrap.init_vault(p, ks)
    monkeypatch.setattr(daemon, "KeyringKeyStore", lambda: ks, raising=False)
    monkeypatch.setattr("whatsvault.crypto.keystore.KeyringKeyStore", lambda: ks)
    v, c, blocked = daemon.open_databases("mcp", check_same_thread=False)
    assert blocked is None
    server.assert_usable_from_worker_threads(vault=v, control=c)  # must not raise


# ---- search is the headline tool and had never been called over the wire --------
def test_search_accepts_the_arguments_a_client_can_actually_send(client):
    """The handler took `q` and passed it straight to reads.search, which expects
    a SearchQuery. Over MCP, `q` arrives as a string — so `search` raised
    AttributeError on every call. The unit tests missed it because they build a
    SearchQuery in Python and call reads.search directly, bypassing the handler
    that is actually registered."""
    hits = _rows(client.call("search", {"q": "hello"}))
    assert [h["message_id"] for h in hits] == ["msg_1"]


def test_search_supports_filtering_by_conversation(client):
    assert _rows(client.call("search", {"q": "hello", "conversation_id": "cnv"}))
    assert _rows(client.call("search", {"q": "hello", "conversation_id": "nope"})) == []


def test_search_result_cap_cannot_be_lifted_by_a_negative_limit(client):
    """SQLite treats LIMIT -1 as unbounded, so min(limit, MAX) is not a cap. A
    model that asks for -1 must not receive the whole table."""
    from whatsvault.search.query import MAX_LIMIT

    hits = _rows(client.call("search", {"q": "hello", "limit": -1}))
    assert 0 <= len(hits) <= MAX_LIMIT


def test_search_rejects_a_non_numeric_limit_without_a_traceback(client):
    result = client.call("search", {"q": "hello", "limit": "all"})
    assert "thread" not in json.dumps(result).lower()


# ---- the model does not get to say what time it is ------------------------------
def test_conversation_window_does_not_take_the_clock_from_the_caller(client):
    """now_ms decided whether a send window was open, and the tool took it as a
    parameter — letting the model assert the time. The server clock is the only
    clock (INV-SENDPOLICY)."""
    assert "now_ms" not in server.WINDOW_TOOL_PARAMETERS
    window = _payload(client.call("get_conversation_window", {"conversation_id": "cnv"}))
    assert window["open"] is False
