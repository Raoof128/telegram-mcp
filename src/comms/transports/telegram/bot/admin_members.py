"""Bot API membership requests (comms v0.3 Task C9): ban, unban, restrict — one call each.

``member.remove`` is deliberately absent: it is the ``(member.ban, member.unban)`` saga in
``SEMANTICS``, run step by step by the executor (A41, G10). Lifting a restriction is
``member.restrict`` with the permissions granted again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from comms.core.providers.capability import Capability as C

__all__ = ["CHAT_PERMISSIONS", "MEMBER_REQUESTS", "take"]

# Bot API ChatPermissions fields.
CHAT_PERMISSIONS = frozenset(
    {
        "can_send_messages",
        "can_send_audios",
        "can_send_documents",
        "can_send_photos",
        "can_send_videos",
        "can_send_video_notes",
        "can_send_voice_notes",
        "can_send_polls",
        "can_send_other_messages",
        "can_add_web_page_previews",
        "can_change_info",
        "can_invite_users",
        "can_pin_messages",
        "can_manage_topics",
    }
)


def positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def boolean(value: object) -> bool:
    return type(value) is bool


def permissions(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(value)
        and set(value) <= CHAT_PERMISSIONS
        and all(type(v) is bool for v in value.values())
    )


Check = Callable[[object], bool]


def take(
    args: Mapping[str, Any], required: Mapping[str, Check], optional: Mapping[str, Check]
) -> dict[str, Any]:
    """The arguments, checked: every required key, no unknown key, every value well-formed."""
    if not set(required) <= set(args) or not set(args) <= set(required) | set(optional):
        raise ValueError("operation arguments are malformed")
    checks = {**required, **optional}
    if not all(checks[k](v) for k, v in args.items()):
        raise ValueError("operation arguments are malformed")
    return {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in args.items()}


def _ban(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    optional = {"until_date": non_negative_int, "revoke_messages": boolean}
    return "banChatMember", {"chat_id": chat_id, **take(args, {"user_id": positive_int}, optional)}


def _unban(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"user_id": positive_int}, {"only_if_banned": boolean})
    return "unbanChatMember", {"chat_id": chat_id, **fields}


def _restrict(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    required = {"user_id": positive_int, "permissions": permissions}
    fields = take(args, required, {"until_date": non_negative_int})
    return "restrictChatMember", {"chat_id": chat_id, **fields}


MEMBER_REQUESTS = {C.MEMBER_BAN: _ban, C.MEMBER_UNBAN: _unban, C.MEMBER_RESTRICT: _restrict}
