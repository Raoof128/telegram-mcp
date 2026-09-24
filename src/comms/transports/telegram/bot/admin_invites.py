"""Bot API invites, join requests and forum topics (comms v0.3 Task C11; A27; P §27, §28).

``invite.create`` and ``topic.create`` are ``CREATE`` (resolve-only on ambiguity); their opaque
provider ref is the new link or thread id, and a success without it is malformed and therefore
``OUTCOME_UNKNOWN`` (A19). Everything else sets a state. The bot cannot list invites or join
requests; those capabilities are ``telegram_user`` only. Hiding the General topic has no
capability id in P and is not offered.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram.bot.args import boolean, positive_int, take, text

__all__ = ["INVITE_REQUESTS", "REF_FIELDS"]

# The documented forum-topic icon colours.
ICON_COLORS = frozenset({7322096, 16766590, 13338331, 9367192, 16749490, 16478047})
REF_FIELDS: Mapping[C, str] = MappingProxyType(
    {C.INVITE_CREATE: "invite_link", C.TOPIC_CREATE: "message_thread_id"}
)


def _link(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 256
        and value.startswith(("https://t.me/+", "https://t.me/joinchat/"))
    )


_INVITE_FIELDS = {
    "name": text(0, 32),
    "expire_date": positive_int,
    "member_limit": lambda v: type(v) is int and 1 <= v <= 99999,
    "creates_join_request": boolean,
}


def _invite_fields(args: Mapping[str, Any], required: Mapping[str, Any]) -> dict[str, Any]:
    fields = take(args, required, _INVITE_FIELDS)
    if fields.get("creates_join_request") is True and "member_limit" in fields:
        raise ValueError("an invite that needs approval cannot have a member limit")
    return fields


def _create_invite(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "createChatInviteLink", {"chat_id": chat_id, **_invite_fields(args, {})}


def _edit_invite(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = _invite_fields(args, {"invite_link": _link})
    if len(fields) < 2:
        raise ValueError("an invite edit changes something")
    return "editChatInviteLink", {"chat_id": chat_id, **fields}


def _revoke_invite(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "revokeChatInviteLink", {"chat_id": chat_id, **take(args, {"invite_link": _link}, {})}


def _approve(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "approveChatJoinRequest", {
        "chat_id": chat_id,
        **take(args, {"user_id": positive_int}, {}),
    }


def _reject(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "declineChatJoinRequest", {
        "chat_id": chat_id,
        **take(args, {"user_id": positive_int}, {}),
    }


def _create_topic(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    optional = {"icon_color": lambda v: v in ICON_COLORS, "icon_custom_emoji_id": text(1, 64)}
    fields = take(args, {"name": text(1, 128)}, optional)
    return "createForumTopic", {"chat_id": chat_id, **fields}


def _edit_topic(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    optional = {"name": text(1, 128), "icon_custom_emoji_id": text(0, 64)}
    fields = take(args, {"message_thread_id": positive_int}, optional)
    if len(fields) < 2:
        raise ValueError("a topic edit changes something")
    return "editForumTopic", {"chat_id": chat_id, **fields}


def _thread(method: str):
    def build(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        return method, {"chat_id": chat_id, **take(args, {"message_thread_id": positive_int}, {})}

    return build


INVITE_REQUESTS = {
    C.INVITE_CREATE: _create_invite,
    C.INVITE_EDIT: _edit_invite,
    C.INVITE_REVOKE: _revoke_invite,
    C.JOIN_REQUEST_APPROVE: _approve,
    C.JOIN_REQUEST_REJECT: _reject,
    C.TOPIC_CREATE: _create_topic,
    C.TOPIC_EDIT: _edit_topic,
    C.TOPIC_CLOSE: _thread("closeForumTopic"),
    C.TOPIC_REOPEN: _thread("reopenForumTopic"),
}
