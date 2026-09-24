"""MCP visibility ACL (ledger #23) — a hard server-side privacy fence.

conversations.mcp_visibility is set ONLY here (invoked by the CLI/phone), never by
an MCP tool (set_mcp_visibility is in the FORBIDDEN surface). The read layer excludes
LOCAL_ONLY conversations in SQL. This does not solve dynamic-scope orchestration; it
is a real hard fence for explicitly-marked conversations."""

_VALID = ("ALLOW_MCP", "LOCAL_ONLY")


def set_visibility(vault_conn, conversation_id: str, visibility: str) -> None:
    if visibility not in _VALID:
        raise ValueError(f"visibility must be one of {_VALID}, got {visibility!r}")
    vault_conn.execute("UPDATE conversations SET mcp_visibility=? WHERE id=?", (visibility, conversation_id))
    vault_conn.commit()


def local_only_ids(vault_conn) -> set:
    rows = vault_conn.execute("SELECT id FROM conversations WHERE mcp_visibility='LOCAL_ONLY'").fetchall()
    return {r[0] for r in rows}
