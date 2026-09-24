"""Argument checks for Telegram requests: one checker, shared by every request table (bot and user)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "CHAT_PERMISSIONS",
    "boolean",
    "non_negative_int",
    "permissions",
    "positive_int",
    "take",
    "text",
]

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


def text(low: int, high: int) -> Check:
    """A string of ``low``..``high`` characters (Bot API limits count characters)."""
    return lambda value: isinstance(value, str) and low <= len(value) <= high
