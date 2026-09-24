"""Bot API membership requests (comms v0.3 Task C9): ban, unban, restrict — one call each.

``member.remove`` is deliberately absent: it is the ``(member.ban, member.unban)`` saga in
``SEMANTICS``, run step by step by the executor (A41, G10). Lifting a restriction is
``member.restrict`` with the permissions granted again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram.args import (
    boolean,
    lifted,
    non_negative_int,
    permissions,
    positive_int,
    take,
)

__all__ = ["MEMBER_REQUESTS"]


def _ban(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    optional = {"until_date": non_negative_int, "revoke_messages": boolean}
    return "banChatMember", {"chat_id": chat_id, **take(args, {"user_id": positive_int}, optional)}


def _unban(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"user_id": positive_int}, {"only_if_banned": boolean})
    return "unbanChatMember", {"chat_id": chat_id, **fields}


def _restrict(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    required = {"user_id": positive_int, "permissions": permissions}
    fields = take(lifted(args), required, {"until_date": non_negative_int})
    return "restrictChatMember", {"chat_id": chat_id, **fields}


MEMBER_REQUESTS = {C.MEMBER_BAN: _ban, C.MEMBER_UNBAN: _unban, C.MEMBER_RESTRICT: _restrict}
