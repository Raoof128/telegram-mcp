"""MCP audit log (ledger #21). Argument hashes are keyed HMACs (audit key in the
keyring), never plain SHA256 — a low-entropy query like a contact name would be
trivially dictionary-recoverable from a bare SHA256. Content is never stored."""

import hashlib
import hmac
import json

from .. import ids

AUDIT_KEY_NAME = "whatsvault.mcp.audit.v1"


def args_hmac(audit_key: bytes, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(audit_key, canonical, hashlib.sha256).hexdigest()


def record(control_conn, audit_key, *, actor, tool, args, outcome, now_ms) -> None:
    control_conn.execute(
        "INSERT INTO audit_log(id, actor, tool, args_hash, outcome, ts_ms) VALUES(?,?,?,?,?,?)",
        (ids.new_id("aud"), actor, tool, args_hmac(audit_key, args), outcome, now_ms),
    )
    control_conn.commit()
