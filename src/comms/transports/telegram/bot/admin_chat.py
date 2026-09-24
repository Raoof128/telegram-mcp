"""Bot API admin rights, chat info and pins (comms v0.3 Task C10; P §26, §74; A27).

Every request sets an exact state (``SET_STATE``). A promotion names a profile or, with
``custom``, its exact rights; "make admin" alone is refused (P §74). Every promotion sends every
right, so the result never depends on what the member held before; a demotion sends every right
false. Updating an admin's rights is a promotion with the new exact rights. Unpinning is
``message.pin`` with ``pinned: false``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram.bot.args import (
    boolean,
    permissions,
    positive_int,
    take,
    text,
)

__all__ = ["ADMIN_RIGHTS", "CHAT_REQUESTS", "PROFILES"]

# Bot API promoteChatMember rights.
ADMIN_RIGHTS = frozenset(
    {
        "is_anonymous",
        "can_manage_chat",
        "can_delete_messages",
        "can_manage_video_chats",
        "can_restrict_members",
        "can_promote_members",
        "can_change_info",
        "can_invite_users",
        "can_post_stories",
        "can_edit_stories",
        "can_delete_stories",
        "can_post_messages",
        "can_edit_messages",
        "can_pin_messages",
        "can_manage_topics",
    }
)


def _profile(*granted: str) -> Mapping[str, bool]:
    assert set(granted) <= ADMIN_RIGHTS
    return MappingProxyType({right: right in granted for right in sorted(ADMIN_RIGHTS)})


PROFILES: Mapping[str, Mapping[str, bool]] = MappingProxyType(
    {
        "moderator": _profile(
            "can_manage_chat", "can_delete_messages", "can_restrict_members", "can_pin_messages"
        ),
        "event_admin": _profile(
            "can_manage_chat",
            "can_invite_users",
            "can_pin_messages",
            "can_manage_video_chats",
            "can_manage_topics",
        ),
        "full_admin": _profile(*(ADMIN_RIGHTS - {"is_anonymous"})),
    }
)


def _rights(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) <= ADMIN_RIGHTS
        and all(type(v) is bool for v in value.values())
        and any(value.values())
    )


def _promote(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if args.get("profile") == "custom":
        required = {"user_id": positive_int, "profile": lambda v: v == "custom", "rights": _rights}
        fields = take(args, required, {})
        rights = {r: fields["rights"].get(r, False) for r in sorted(ADMIN_RIGHTS)}
    else:
        fields = take(
            args,
            {"user_id": positive_int, "profile": lambda v: isinstance(v, str) and v in PROFILES},
            {},
        )
        rights = dict(PROFILES[fields["profile"]])
    return "promoteChatMember", {"chat_id": chat_id, "user_id": fields["user_id"], **rights}


def _demote(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"user_id": positive_int}, {})
    return "promoteChatMember", {"chat_id": chat_id, **fields, **dict.fromkeys(ADMIN_RIGHTS, False)}


def _title(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "setChatTitle", {"chat_id": chat_id, **take(args, {"title": text(1, 128)}, {})}


def _description(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"description": text(0, 255)}, {})
    return "setChatDescription", {"chat_id": chat_id, **fields}


def _permissions(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"permissions": permissions}, {"use_independent_chat_permissions": boolean})
    return "setChatPermissions", {"chat_id": chat_id, **fields}


def _pin(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if args.get("pinned") is True:
        fields = take(
            args,
            {"message_id": positive_int, "pinned": boolean},
            {"disable_notification": boolean},
        )
        del fields["pinned"]
        return "pinChatMessage", {"chat_id": chat_id, **fields}
    fields = take(args, {"message_id": positive_int, "pinned": lambda v: v is False}, {})
    return "unpinChatMessage", {"chat_id": chat_id, "message_id": fields["message_id"]}


CHAT_REQUESTS = {
    C.ADMIN_PROMOTE: _promote,
    C.ADMIN_DEMOTE: _demote,
    C.CHAT_SET_TITLE: _title,
    C.CHAT_SET_DESCRIPTION: _description,
    C.CHAT_SET_PERMISSIONS: _permissions,
    C.MESSAGE_PIN: _pin,
}
