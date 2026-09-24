"""MCP read query layer (spec §5) — composes search + present, and enforces the
hard LOCAL_ONLY privacy fence (#23). Every result is redacted and untrusted-wrapped;
LOCAL_ONLY conversations never leave the boundary. No write verb exists here."""

from ..ingest.status import reduce_status
from ..search.query import run as _search_run
from . import acl, present

WINDOW_MS = 24 * 3600 * 1000
MAX_LIMIT = 200


def _clamp(limit) -> int:
    """min(limit, MAX_LIMIT) is not a cap: SQLite treats LIMIT -1 as unbounded,
    so a negative value returned the whole table. Clamp both ends."""
    try:
        n = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    return max(1, min(n, MAX_LIMIT))


def _contact(vault_conn, contact_id):
    if not contact_id:
        return None
    return vault_conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()


def _view(vault_conn, msg_row):
    return present.message_view(msg_row, _contact(vault_conn, msg_row["sender_contact_id"]))


def search(vault_conn, q) -> list:
    local = acl.local_only_ids(vault_conn)
    out = []
    for r in _search_run(vault_conn, q):
        if r.get("conversation_id") in local:
            continue
        m = vault_conn.execute("SELECT * FROM messages WHERE id=?", (r["message_id"],)).fetchone()
        if not m:
            continue
        v = _view(vault_conn, m)
        v["rank"] = r["rank"]
        v["tier"] = r["tier"]
        out.append(v)
    return out


def get_messages(vault_conn, conversation_id, from_ms=None, to_ms=None, limit=50) -> list:
    vis = vault_conn.execute(
        "SELECT mcp_visibility FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    if not vis or vis[0] == "LOCAL_ONLY":
        return []
    preds, params = ["conversation_id=?"], [conversation_id]
    if from_ms is not None:  # interval overlap
        preds.append("ts_upper_ms_exclusive > ?")
        params.append(from_ms)
    if to_ms is not None:
        preds.append("ts_lower_ms < ?")
        params.append(to_ms)
    # Every element of `preds` is a module-literal fragment; all caller values are
    # bound parameters. No user input reaches the SQL text.
    sql = "SELECT * FROM messages WHERE " + " AND ".join(preds) + " ORDER BY ts_lower_ms, id LIMIT ?"
    rows = vault_conn.execute(sql, [*params, _clamp(limit)]).fetchall()
    return [_view(vault_conn, m) for m in rows]


def list_chats(vault_conn, query=None, limit=20) -> list:
    preds = ["mcp_visibility != 'LOCAL_ONLY'"]
    params = []
    if query:
        preds.append("subject LIKE ?")
        params.append(f"%{query}%")
    sql = (
        "SELECT id, type, subject, last_message_ms FROM conversations WHERE "
        + " AND ".join(preds)
        + " ORDER BY last_message_ms DESC LIMIT ?"
    )
    rows = vault_conn.execute(sql, [*params, _clamp(limit)]).fetchall()
    return [
        {
            "conversation_id": r["id"],
            "type": r["type"],
            "subject": present.untrusted(r["subject"]),
            "last_message_ms": r["last_message_ms"],
        }
        for r in rows
    ]


def get_message_status(vault_conn, message_id):
    m = vault_conn.execute("SELECT wamid, conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()
    if not m:
        return None
    if m["conversation_id"] in acl.local_only_ids(vault_conn):
        return None
    if not m["wamid"]:
        return {"delivery_rank": 0, "failed_at_ms": None, "deleted_at_ms": None, "unknown_statuses": []}
    events = [
        {"status": r["status"], "provider_ts_ms": r["provider_ts_ms"]}
        for r in vault_conn.execute(
            "SELECT status, provider_ts_ms FROM message_status_events WHERE wamid=?", (m["wamid"],)
        ).fetchall()
    ]
    return reduce_status(events)


def get_conversation_window(control_conn, vault_conn, conversation_id, now_ms) -> dict:
    """LOCAL_ONLY fence applies here too (#23): activity timing is content. A
    caller must not learn when a conversation marked private last received a
    message, so a fenced conversation is indistinguishable from an idle one."""
    if conversation_id in acl.local_only_ids(vault_conn):
        return {"open": False, "last_inbound_ms": 0, "closes_at_ms": WINDOW_MS}
    row = control_conn.execute(
        "SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    last = row[0] if row else 0
    closes = last + WINDOW_MS
    return {"open": bool(last > 0 and now_ms < closes), "last_inbound_ms": last, "closes_at_ms": closes}


def list_templates(control_conn) -> dict:
    exists = control_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='templates'"
    ).fetchone()
    if not exists:
        return {"status": "FEATURE_NOT_INITIALISED", "templates": []}
    rows = control_conn.execute(
        "SELECT template_id, name, language, category, status FROM templates"
    ).fetchall()
    return {"status": "OK", "templates": [dict(r) for r in rows]}
