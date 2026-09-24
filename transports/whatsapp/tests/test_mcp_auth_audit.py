import hashlib
import os

from whatsvault.crypto.keystore import MemoryKeyStore
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import audit, auth


def test_token_roundtrip_and_reject():
    ks = MemoryKeyStore()
    tok = auth.provision_token(ks)
    assert auth.require_token(tok, tok) is True
    assert auth.require_token("deadbeef", tok) is False
    assert auth.require_token("", tok) is False
    assert auth.require_token(None, tok) is False


def test_args_hmac_is_keyed_not_plain_sha256():
    k1, k2 = os.urandom(32), os.urandom(32)
    args = {"query": "Mona"}
    h1 = audit.args_hmac(k1, args)
    plain = hashlib.sha256(b'{"query":"Mona"}').hexdigest()
    assert h1 != plain  # keyed HMAC, not a guessable SHA256
    assert h1 != audit.args_hmac(k2, args)  # different key -> different digest
    assert h1 == audit.args_hmac(k1, args)  # stable under the same key


def test_record_appends_hashed_never_content(tmp_path):
    ctrl = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(ctrl, "control")
    k = os.urandom(32)
    audit.record(ctrl, k, actor="mcp", tool="search", args={"query": "Mona"}, outcome="ok", now_ms=123)
    row = ctrl.execute("SELECT actor, tool, args_hash, outcome FROM audit_log").fetchone()
    assert row["actor"] == "mcp" and row["tool"] == "search" and row["outcome"] == "ok"
    assert "Mona" not in row["args_hash"] and len(row["args_hash"]) == 64
