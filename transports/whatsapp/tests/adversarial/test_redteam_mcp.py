"""Red-team gate for the MCP read surface (spec §5.5/§5.8, ledger #19/#21/#23).

Each test here began as a confirmed exploit against the shipped surface. They
encode the attacker's goal, not the implementation, so a regression re-opens the
hole visibly.
"""

import asyncio
import base64
import os
import re

import pytest

from apps.mcp import server
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import acl, reads
from whatsvault.mcp.http_auth import BearerAuthMiddleware
from whatsvault.search import index as IDX
from whatsvault.search.query import SearchQuery

SECRET_NUMBER = "61412345678"
# Real wamid shape: the base64 payload carries the counterparty E.164 in the clear.
REPLY_WAMID = (
    "wamid."
    + base64.b64encode(
        b"\x1c\x18\x0b" + SECRET_NUMBER.encode() + b"\x15\x02\x00\x11\x18\x129B3F7A8C"
    ).decode()
)


@pytest.fixture
def db(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    v.execute("INSERT INTO accounts(id,phone_number_id) VALUES('acc','pn')")
    for cid in ("pub", "secret"):
        v.execute("INSERT INTO conversations(id,account_id,type,subject) VALUES(?,'acc','dm',?)", (cid, cid))
    v.commit()
    return v, c


def _msg(v, mid, conv, body, ts=1, reply_to=None):
    v.execute(
        "INSERT INTO messages(id,account_id,conversation_id,direction,ts_lower_ms,"
        "ts_upper_ms_exclusive,ts_precision,type,text_original,origin,window_eligible,"
        "reply_to_wamid) VALUES(?, 'acc',?, 'in',?,?,'min','text',?,'manual_export',0,?)",
        (mid, conv, ts, ts + 1, body, reply_to),
    )
    IDX.index_message(v, mid, body)
    v.commit()


# --- Finding 1: LOCAL_ONLY fence was not applied to the window tool -------------
def test_local_only_conversation_leaks_no_window_metadata(db):
    """acl.py calls LOCAL_ONLY 'a hard fence'. Activity timing is still content."""
    v, c = db
    acl.set_visibility(v, "secret", "LOCAL_ONLY")
    c.execute(
        "INSERT INTO conversation_windows(conversation_id,last_inbound_ms) VALUES('secret',1700000000000)"
    )
    c.commit()
    out = reads.get_conversation_window(c, v, "secret", 1700000001000)
    assert out["last_inbound_ms"] == 0, "leaked exact last-inbound timestamp of a private chat"
    assert out["open"] is False


def test_allowed_conversation_window_still_works(db):
    v, c = db
    c.execute("INSERT INTO conversation_windows(conversation_id,last_inbound_ms) VALUES('pub',1700000000000)")
    c.commit()
    out = reads.get_conversation_window(c, v, "pub", 1700000001000)
    assert out["open"] is True and out["last_inbound_ms"] == 1700000000000


# --- Finding 2: wamid base64 carries the full phone number ---------------------
def test_reply_wamid_does_not_leak_the_phone_number(db):
    """§5.8 says never return a full wa_id. A raw wamid is one base64 decode away."""
    v, _ = db
    _msg(v, "m1", "pub", "a reply", reply_to=REPLY_WAMID)
    view = reads.get_messages(v, "pub")[0]
    blob = repr(view)
    assert SECRET_NUMBER not in blob
    for token in re.findall(r"[A-Za-z0-9+/=]{16,}", blob):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=False)
        except Exception:
            continue
        assert SECRET_NUMBER.encode() not in raw, f"phone number recoverable from {token!r}"


def test_reply_reference_is_still_correlatable(db):
    """Redaction must not destroy the ability to link a reply chain."""
    v, _ = db
    _msg(v, "m1", "pub", "first", ts=1, reply_to=REPLY_WAMID)
    _msg(v, "m2", "pub", "second", ts=2, reply_to=REPLY_WAMID)
    a, b = reads.get_messages(v, "pub")
    assert a["reply_to_ref"] and a["reply_to_ref"] == b["reply_to_ref"]


# --- Finding 3: negative limit defeated MAX_LIMIT ------------------------------
@pytest.mark.parametrize("bad", [-1, 0, -999])
def test_limit_cannot_be_escaped_downward(db, bad):
    v, _ = db
    for i in range(reads.MAX_LIMIT + 30):
        _msg(v, f"m{i:04d}", "pub", f"body {i}", ts=i + 1)
    rows = reads.get_messages(v, "pub", limit=bad)
    assert 0 < len(rows) <= reads.MAX_LIMIT, f"limit={bad} returned {len(rows)} rows"


def test_list_chats_limit_cannot_be_escaped_downward(db):
    v, _ = db
    for i in range(reads.MAX_LIMIT + 30):
        v.execute(
            "INSERT INTO conversations(id,account_id,type,subject) VALUES(?,'acc','dm',?)",
            (f"c{i:04d}", f"s{i}"),
        )
    v.commit()
    assert len(reads.list_chats(v, limit=-1)) <= reads.MAX_LIMIT


# --- Finding 4: the audit log recorded every call as a success -----------------
def test_failed_tool_call_is_audited_as_a_failure(db):
    """A probe that errors must not leave a clean trail (§5.8)."""
    v, c = db
    handlers = server.build_tool_handlers(v, c, os.urandom(32))
    with pytest.raises(ValueError):  # _clamp rejects a non-integer limit
        handlers["get_messages"](conversation_id="pub", limit="not-an-int")
    rows = c.execute("SELECT tool, outcome FROM audit_log").fetchall()
    assert rows, "a failed call must still be audited"
    assert all(r["outcome"] != "ok" for r in rows), "failure recorded as success"


def test_successful_tool_call_is_audited_as_ok(db):
    v, c = db
    handlers = server.build_tool_handlers(v, c, os.urandom(32))
    handlers["list_templates"]()
    row = c.execute("SELECT tool, outcome FROM audit_log").fetchone()
    assert row["tool"] == "list_templates" and row["outcome"] == "ok"


def test_audit_never_stores_plaintext_query_terms(db):
    v, c = db
    handlers = server.build_tool_handlers(v, c, os.urandom(32))
    handlers["search"](q=SearchQuery(terms=["extremely-distinctive-needle"]))
    dump = repr(c.execute("SELECT * FROM audit_log").fetchall())
    assert "extremely-distinctive-needle" not in dump


# --- Finding 5: ambiguous Authorization headers --------------------------------
def _drive(mw, headers):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    asyncio.run(mw({"type": "http", "method": "POST", "path": "/mcp", "headers": headers}, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_duplicate_authorization_headers_are_refused():
    """Ambiguous credentials must fail closed, not first-wins."""
    mw = BearerAuthMiddleware(_ok, "tok")
    both = [(b"authorization", b"Bearer tok"), (b"authorization", b"Bearer wrong")]
    assert _drive(mw, both) == 401
    assert _drive(mw, list(reversed(both))) == 401


def test_single_valid_header_still_passes():
    mw = BearerAuthMiddleware(_ok, "tok")
    assert _drive(mw, [(b"authorization", b"Bearer tok")]) == 200
