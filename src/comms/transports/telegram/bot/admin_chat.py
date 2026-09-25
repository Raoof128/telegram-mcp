"""Bot API admin rights, chat info and pins (comms v0.3 Task C10; P §26, §74; A27).

Every request sets an exact state (``SET_STATE``). A promotion names a profile or, with
``custom``, its exact rights; "make admin" alone is refused (P §74). Every promotion sends every
right, so the result never depends on what the member held before; a demotion sends every right
false. Updating an admin's rights is a promotion with the new exact rights. Unpinning is
``message.pin`` with ``pinned: false``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram import chat_specs as specs
from comms.transports.telegram.admin_profiles import ADMIN_RIGHTS, PROFILES, promotion
from comms.transports.telegram.args import (
    boolean,
    permissions,
    positive_int,
    take,
)

__all__ = ["ADMIN_RIGHTS", "CHAT_REQUESTS", "PROFILES"]  # the profiles are re-exported


def _promote(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    user_id, rights = promotion(args)
    return "promoteChatMember", {"chat_id": chat_id, "user_id": user_id, **rights}


def _demote(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"user_id": positive_int}, {})
    return "promoteChatMember", {"chat_id": chat_id, **fields, **dict.fromkeys(ADMIN_RIGHTS, False)}


def _title(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "setChatTitle", {"chat_id": chat_id, **specs.set_title(args)}


def _description(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    return "setChatDescription", {"chat_id": chat_id, **specs.set_description(args)}


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
