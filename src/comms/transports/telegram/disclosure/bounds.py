"""Worst-case record shapes for step 3's estimate (Phase-3 design §5.4).

Step 11 refuses when the actual charge exceeds the reservation, so an
estimate that is too small is a disclosure that never happens, and an
estimate that is merely large is an honest, conservative reservation. Every
string the Telegram reads emit has a ceiling here; the reads clamp to it
(``clamp``), so the bound holds by construction, not by hope. The bound is
measured, not hand-counted: a record built from the ceilings, filled with
U+0001 (TG-JCS escapes it to six bytes, more than any other codepoint).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from comms.core.canonical import jcs_dumps
from comms.transports.telegram.page_bounds import (
    DATA_BYTES_MAX,
    MEDIA_KIND_MAX,
    NAME_MAX,
    TEXT_CODEPOINTS_MAX,
    TEXT_MAX,
    USERNAME_MAX,
    PageBudget,
    clamp,
    fit,
    text_codepoints,
)

__all__ = [
    "DATA_BYTES_MAX",
    "MEDIA_KIND_MAX",
    "NAME_MAX",
    "TEXT_CODEPOINTS_MAX",
    "TEXT_MAX",
    "USERNAME_MAX",
    "PageBudget",
    "clamp",
    "fit",
    "text_codepoints",
    "worst_case",
]

_W = "\x01"
_INT = 2**63 - 1
_DATE = "2026-01-01T00:00:00Z"
_PEER = "tgp_" + "a" * 26
_MSG = "tgm_" + "a" * 26


def _text_bound(egress_level: str, excerpt_limit: int | None) -> str | None:
    if egress_level == "metadata_only":
        return None
    if egress_level == "excerpt":
        if excerpt_limit is None:
            raise ValueError("excerpt level requires a limit")
        return _W * min(excerpt_limit, TEXT_MAX)
    if egress_level == "full_text":
        return _W * TEXT_MAX
    raise ValueError("unknown egress level")


def _record(
    tool_name: str, project_ref: str, text: str | None, projects: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    name, user = _W * NAME_MAX, _W * USERNAME_MAX
    if tool_name == "telegram_list_chats":
        return {
            "origin_project_refs": [project_ref],
            "peer_ref": _PEER,
            "display_name": name,
            "username": user,
            "chat_type": "supergroup",
            "unread_count": _INT,
            "is_archived": False,
            "is_muted": False,
            "last_message_at": _DATE,
        }
    if tool_name == "telegram_resolve_peer":
        return {
            "peer_ref": _PEER,
            "display_name": name,
            "username": user,
            "chat_type": "supergroup",
            "match_kind": "substring_display_name",
        }
    if tool_name == "telegram_get_unread":
        return {
            "peer_ref": _PEER,
            "display_name": name,
            "chat_type": "supergroup",
            "unread_count": _INT,
            "is_muted": False,
            "last_message_at": _DATE,
        }
    if tool_name in ("telegram_get_messages", "telegram_get_context"):
        return {
            "message_ref": _MSG,
            "origin_project_refs": [project_ref],
            "sender_kind": "anonymous_admin",
            "sender_display_name": name,
            "sender_peer_ref": _PEER,
            "post_author": name,
            "forum_topic": False,
            "topic_title": None,
            "sent_at": _DATE,
            "outgoing": False,
            "text": text,
            "text_truncated": False,
            "reply_to_message_ref": _MSG,
            "has_media": False,
            "media_kind": _W * MEDIA_KIND_MAX,
            "edited": False,
        }
    if tool_name in ("telegram_search_messages", "telegram_cross_project_search"):
        hit: dict[str, Any] = {
            "origin_project_refs": [project_ref],
            "peer_ref": _PEER,
            "peer_display_name": name,
            "message_ref": _MSG,
            "sender_kind": "anonymous_admin",
            "sender_display_name": name,
            "sent_at": _DATE,
            "text": text,
            "text_truncated": False,
            "has_context": False,
        }
        if tool_name == "telegram_cross_project_search":
            # Every selected project may match: the widest record names them all.
            hit["origin_project_refs"] = [ref for ref, _ in projects]
            hit["matched_projects"] = [
                {"project_ref": ref, "display_name": display} for ref, display in projects
            ]
        return hit
    raise ValueError("no bound for this tool")


_ELEMENT = {
    "telegram_list_chats": "chats",
    "telegram_resolve_peer": "matches",
    "telegram_get_unread": "chats",
    "telegram_get_messages": "messages",
    "telegram_get_context": "messages",
    "telegram_search_messages": "results",
    "telegram_cross_project_search": "results",
}


def worst_case(
    tool_name: str,
    *,
    limit: int,
    project_ref: str,
    project_display_name: str,
    egress_level: str,
    excerpt_limit: int | None,
    projects: Sequence[tuple[str, str]] = (),
) -> tuple[int, int, int]:
    """``(records, global_bytes, project_bytes)`` for a full page, capped at the page cap.

    ``projects`` is the selected set for cross-project search, as
    ``(project_ref, display_name)``; the egress bound must then be the
    *widest* selected grant, since each record takes its own intersection.
    """
    if tool_name == "telegram_cross_project_search" and not projects:
        raise ValueError("cross-project bounds need the selected projects")
    record = _record(tool_name, project_ref, _text_bound(egress_level, excerpt_limit), projects)
    data: dict[str, Any] = {_ELEMENT[tool_name]: [record] * limit}
    if tool_name == "telegram_cross_project_search":
        data["projects"] = [{"project_ref": ref, "display_name": d} for ref, d in projects]
        data["search_scope"] = "cross_project"
    else:
        data["project"] = {"project_ref": project_ref, "display_name": project_display_name}
    if tool_name == "telegram_search_messages":
        data["search_scope"] = "project"
    elif tool_name == "telegram_get_context":
        data["peer"] = {"peer_ref": _PEER, "display_name": _W * NAME_MAX}
        data["anchor_message_ref"] = _MSG
    elif tool_name == "telegram_get_messages":
        data["peer"] = {"peer_ref": _PEER, "display_name": _W * NAME_MAX, "chat_type": "supergroup"}
    elif tool_name == "telegram_get_unread":
        data["total_unread_visible"] = _INT
        data["total_is_exact"] = False
    elif tool_name == "telegram_resolve_peer":
        data["ambiguous"] = False
    total = min(len(jcs_dumps(data)), DATA_BYTES_MAX)
    return limit, total, min(limit * len(jcs_dumps(record)), DATA_BYTES_MAX)
