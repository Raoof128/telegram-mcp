"""MTProto membership and admin-rights requests, validated (comms v0.3 Task C18; P §25, §26, §74).

Pure: each entry checks its arguments and returns the neutral spec the adapter builds its one
RPC from. ``member.remove`` is absent: it is the ``(member.ban, member.unban)`` saga (A41). A
failed direct add is ``INVITE_REQUIRED`` and never becomes an invite on its own (P §25). An
MTProto unban lifts every restriction and never removes a member, so ``only_if_banned`` (the Bot
API's guard against kicking a member) holds by construction and is accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram.admin_profiles import ADMIN_RIGHTS, promotion
from comms.transports.telegram.args import (
    boolean,
    non_negative_int,
    permissions,
    positive_int,
    take,
)

__all__ = ["MEMBER_SPECS"]


def _add(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {"user_id": positive_int}, {})


def _ban(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {"user_id": positive_int}, {"until_date": non_negative_int})


def _unban(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {"user_id": positive_int}, {"only_if_banned": boolean})


def _restrict(args: Mapping[str, Any]) -> dict[str, Any]:
    required = {"user_id": positive_int, "permissions": permissions}
    return take(args, required, {"until_date": non_negative_int})


def _promote(args: Mapping[str, Any]) -> dict[str, Any]:
    user_id, rights = promotion(args)
    return {"user_id": user_id, "rights": rights}


def _demote(args: Mapping[str, Any]) -> dict[str, Any]:
    fields = take(args, {"user_id": positive_int}, {})
    return {**fields, "rights": dict.fromkeys(sorted(ADMIN_RIGHTS), False)}


MEMBER_SPECS = {
    C.MEMBER_ADD: _add,
    C.MEMBER_BAN: _ban,
    C.MEMBER_UNBAN: _unban,
    C.MEMBER_RESTRICT: _restrict,
    C.ADMIN_PROMOTE: _promote,
    C.ADMIN_DEMOTE: _demote,
}
