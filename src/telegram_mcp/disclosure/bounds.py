"""Worst-case record shapes for step 3's estimate (Phase-3 design §5.4).

Step 11 refuses when the actual charge exceeds the reservation, so an
estimate that is too small is a disclosure that never happens, and an
estimate that is merely large is an honest, conservative prompt. Every
string the Telegram reads emit has a ceiling here; the reads clamp to it
(``clamp``), so the bound holds by construction, not by hope. The bound is
measured, not hand-counted: a record built from the ceilings, filled with
U+0001 (TG-JCS escapes it to six bytes, more than any other codepoint).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

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

NAME_MAX = 256  # display names, chat titles, post authors (codepoints)
USERNAME_MAX = 64
TEXT_MAX = 4096  # Telegram's message ceiling; anything longer is truncated
MEDIA_KIND_MAX = 32
# The dispatcher refuses a response over 64 KiB, after the commit. Canonical
# data is held to 48 KiB so meta, the proof and the envelope always fit.
DATA_BYTES_MAX = 49_152
# Spec §13.2: the combined textual payload of one result, in codepoints. Every
# string in ``data`` counts (conservative: names and refs as well as bodies).
TEXT_CODEPOINTS_MAX = 32_000

_W = "\x01"
_INT = 2**63 - 1
_DATE = "2026-01-01T00:00:00Z"
_PEER = "tgp_" + "a" * 26
_MSG = "tgm_" + "a" * 26


def clamp(text: str | None, limit: int) -> tuple[str | None, bool]:
    """Truncate to ``limit`` codepoints; make lone surrogates encodable."""
    if text is None:
        return None, False
    text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def text_codepoints(value: Any) -> int:
    """Codepoints of every string in ``value`` (keys excluded)."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(text_codepoints(v) for v in value.values())
    if isinstance(value, list):
        return sum(text_codepoints(v) for v in value)
    return 0


class PageBudget:
    """Both §13.2 response caps for one page, in one place.

    Built from the page's container (everything but the records); ``take``
    admits a record only if the canonical bytes stay within
    ``DATA_BYTES_MAX`` *and* the combined text stays within
    ``TEXT_CODEPOINTS_MAX``. Enforced while the page is built, before the
    disclosure commit, never after it.
    """

    def __init__(self, container: dict[str, Any]) -> None:
        self._bytes = DATA_BYTES_MAX - len(jcs_dumps(container))
        self._points = TEXT_CODEPOINTS_MAX - text_codepoints(container)
        self._kept = 0

    def take(self, record: Any) -> bool:
        size = len(jcs_dumps(record)) + (1 if self._kept else 0)  # the separating comma
        points = text_codepoints(record)
        if size > self._bytes or points > self._points:
            return False
        self._bytes -= size
        self._points -= points
        self._kept += 1
        return True


def fit(data: dict[str, Any], element: str) -> int:
    """Keep the longest record prefix that fits both caps; return drops."""
    records = data[element]
    budget = PageBudget({**data, element: []})
    kept = 0
    for record in records:
        if not budget.take(record):
            break
        kept += 1
    dropped = len(records) - kept
    del records[kept:]
    return dropped


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
