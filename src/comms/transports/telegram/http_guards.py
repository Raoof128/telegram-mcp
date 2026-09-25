"""The legacy Telegram listeners' guards: the generic ones live in ``comms.http_guards``
(one copy, comms v0.3 D28); only the per-tool limits of the retired Telegram tools stay here.
"""

from __future__ import annotations

from comms.http_guards import (
    PARSE_ERROR_BODY,
    RATE_LIMITED_BODY,
    UNAUTHORIZED_BODY,
    RateLimiter,
    bearer_gate,
    duplicate_key_preflight,
    no_store,
)

__all__ = [
    "DEFAULT_LIMITS",
    "PARSE_ERROR_BODY",
    "RATE_LIMITED_BODY",
    "UNAUTHORIZED_BODY",
    "RateLimiter",
    "bearer_gate",
    "duplicate_key_preflight",
    "no_store",
]

# Spec §28 per-minute defaults. The catalogue tools and resolve_peer are not
# in the table; they take the ordinary 30.
DEFAULT_LIMITS: dict[str, int] = {
    "telegram_status": 60,
    "telegram_list_projects": 30,
    "telegram_resolve_project": 30,
    "telegram_list_chats": 30,
    "telegram_resolve_peer": 30,
    "telegram_get_messages": 30,
    "telegram_get_context": 30,
    "telegram_get_unread": 30,
    "telegram_search_messages": 20,
    "telegram_cross_project_search": 20,
}
