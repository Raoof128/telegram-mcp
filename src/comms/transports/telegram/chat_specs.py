"""Telegram chat-administration arguments, checked once for both actors (comms v0.3 C11, C19).

Each function takes an operation's arguments and returns the checked, provider-neutral spec
(named as P names them), or raises ``ValueError``. The bot passes a spec straight to its Bot API
method; the adapter builds the MTProto request from the same spec.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from comms.transports.telegram.args import boolean, permissions, positive_int, take, text

__all__ = [
    "ICON_COLORS",
    "invite_create",
    "invite_edit",
    "invite_revoke",
    "member",
    "set_description",
    "set_permissions",
    "set_title",
    "thread",
    "topic_create",
    "topic_edit",
]

Spec = dict[str, Any]
# The documented forum-topic icon colours.
ICON_COLORS = frozenset({7322096, 16766590, 13338331, 9367192, 16749490, 16478047})


def _link(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 256
        and value.startswith(("https://t.me/+", "https://t.me/joinchat/"))
    )


_INVITE_FIELDS: Mapping[str, Callable[[object], bool]] = {
    "name": text(0, 32),
    "expire_date": positive_int,
    "member_limit": lambda v: type(v) is int and 1 <= v <= 99999,
    "creates_join_request": boolean,
}


def _invite_fields(args: Mapping[str, Any], required: Mapping[str, Any]) -> Spec:
    fields = take(args, required, _INVITE_FIELDS)
    if fields.get("creates_join_request") is True and "member_limit" in fields:
        raise ValueError("an invite that needs approval cannot have a member limit")
    return fields


def invite_create(args: Mapping[str, Any]) -> Spec:
    return _invite_fields(args, {})


def invite_edit(args: Mapping[str, Any]) -> Spec:
    fields = _invite_fields(args, {"invite_link": _link})
    if len(fields) < 2:
        raise ValueError("an invite edit changes something")
    return fields


def invite_revoke(args: Mapping[str, Any]) -> Spec:
    return take(args, {"invite_link": _link}, {})


def member(args: Mapping[str, Any]) -> Spec:
    return take(args, {"user_id": positive_int}, {})


def topic_create(args: Mapping[str, Any]) -> Spec:
    optional = {"icon_color": lambda v: v in ICON_COLORS, "icon_custom_emoji_id": _emoji}
    return take(args, {"name": text(1, 128)}, optional)


def topic_edit(args: Mapping[str, Any]) -> Spec:
    optional = {"name": text(1, 128), "icon_custom_emoji_id": _emoji}
    fields = take(args, {"message_thread_id": positive_int}, optional)
    if len(fields) < 2:
        raise ValueError("a topic edit changes something")
    return fields


def thread(args: Mapping[str, Any]) -> Spec:
    return take(args, {"message_thread_id": positive_int}, {})


def set_title(args: Mapping[str, Any]) -> Spec:
    return take(args, {"title": text(1, 128)}, {})


def set_description(args: Mapping[str, Any]) -> Spec:
    return take(args, {"description": text(0, 255)}, {})


def set_permissions(args: Mapping[str, Any]) -> Spec:
    return take(args, {"permissions": permissions}, {})


def _emoji(value: object) -> bool:
    """A custom-emoji document id: a decimal string, as the Bot API passes it."""
    return isinstance(value, str) and 1 <= len(value) <= 20 and value.isascii() and value.isdigit()
