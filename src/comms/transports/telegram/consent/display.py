# src/telegram_mcp/consent/display.py
"""The five-field consent display (spec §9.8 step 6).

The Swift agent renders exactly ``client_display``, ``action_display``,
``project_display``, ``peer_display`` and ``risk_class``, strips controls
and bidi, and caps each at 160 codepoints. So the egress level and the
budget quantities §9.8 requires ride in ``risk_class``, and no agent change
is needed. The digest covers these exact bytes, so what is shown is what is
signed.

Labels come from fixed tables, never from caller text: "Caller-provided text
MUST NOT determine the security-sensitive prompt summary" (§9.8).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from comms.transports.telegram.disclosure.budget import Usage

__all__ = ["ACTION_DISPLAY", "CLIENT_DISPLAY", "build_display"]

ACTION_DISPLAY: dict[str, str] = {
    "telegram_list_projects": "list gateway projects",
    "telegram_resolve_project": "resolve a gateway project",
    "telegram_list_chats": "list chats",
    "telegram_resolve_peer": "resolve a chat",
    "telegram_get_unread": "list unread chats",
    "telegram_get_messages": "read messages",
    "telegram_get_context": "read message context",
    "telegram_search_messages": "search messages",
    "telegram_cross_project_search": "Cross-project disclosure: search messages",
}

CLIENT_DISPLAY: dict[str, str] = {
    "codex_local": "Codex",
    "claude_code_local": "Claude Code",
    "openai_tunnel": "ChatGPT",
}

_CAP = 160


def build_display(
    *,
    tool_name: str,
    client_kind: str,
    project_names: Sequence[str],
    peer_name: str | None,
    egress_level: str,
    tier: str,
    current: Usage,
    projected: Usage,
) -> dict[str, Any]:
    """Build the display payload whose digest the challenge signs."""
    if tool_name not in ACTION_DISPLAY:
        raise ValueError("no display for this tool")
    if client_kind not in CLIENT_DISPLAY:
        raise ValueError("unknown client kind")
    warning = "ELEVATED " if tier == "elevated" else ""
    # §21A.4: a cross-project prompt warns that results may enter the AI's context.
    crossing = (
        "results may enter this AI session's context; "
        if tool_name == "telegram_cross_project_search"
        else ""
    )
    risk = (
        f"{crossing}{egress_level}; {warning}budget {current.records}->{projected.records}"
        f" records, {current.bytes}->{projected.bytes} bytes"
    )
    return {
        "action_display": ACTION_DISPLAY[tool_name],
        "client_display": CLIENT_DISPLAY[client_kind],
        "peer_display": peer_name,
        "project_display": list(project_names),
        "risk_class": risk[:_CAP],
    }
