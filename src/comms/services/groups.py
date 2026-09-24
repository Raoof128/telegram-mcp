"""Group admin services: membership and admin rights (comms v0.3 Task D12; P §25, §26, §73, §74).

Every write goes the same way: the tool names one capability per transport (a tool a transport
cannot perform is ``PROVIDER_UNSUPPORTED``, never a different operation, P §25); the actor is
chosen by capability and preference; a fresh snapshot must say ``AVAILABLE``; and the call runs
through ``MutationExecutor.provider`` with its semantics, replay and audit. The result is
structured truth (P §73): a provider refusal is ``FAILED`` with its code, never success.

A member is named by recipient ref and resolved to that recipient's identity on the group's
transport. Promotion takes an explicit rights profile (P §74); the adapter validates it before
anything is recorded, so "make admin" alone is ``INVALID_ARGUMENT``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from comms.core.campaigns.directory import member_identity
from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.services.capability import CapabilityService
from comms.services.mutations import CallContext, MutationExecutor

__all__ = ["MEMBERSHIP", "GroupService"]

# tool → the capability that performs it on each transport (P §25–26).
MEMBERSHIP: Mapping[str, Mapping[str, C]] = MappingProxyType(
    {
        "group.member.add": {"telegram": C.MEMBER_ADD},
        "group.member.remove": {"telegram": C.MEMBER_REMOVE, "whatsapp": C.GROUP_MEMBER_REMOVE},
        "group.member.ban": {"telegram": C.MEMBER_BAN},
        "group.member.unban": {"telegram": C.MEMBER_UNBAN},
        "group.member.restrict": {"telegram": C.MEMBER_RESTRICT},
        "group.member.unrestrict": {"telegram": C.MEMBER_RESTRICT},
        "group.admin.promote": {"telegram": C.ADMIN_PROMOTE},
        "group.admin.demote": {"telegram": C.ADMIN_DEMOTE},
        "group.admin.update_rights": {"telegram": C.ADMIN_PROMOTE},
    }
)
# Arguments a tool fixes itself, never taken from the caller.
_FIXED: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {"group.member.unrestrict": {"permissions": "all"}}
)


def _telegram_member(identity: str) -> dict[str, Any]:
    if not identity.isdigit():  # a marked chat id is not a person
        raise CommsError("INVALID_ARGUMENT")
    return {"user_id": int(identity)}


# The member argument each transport's adapters take, from the stored identity.
_MEMBER_ARG: Mapping[str, Callable[[str], dict[str, Any]]] = MappingProxyType(
    {
        "telegram": _telegram_member,
        "whatsapp": lambda identity: {"wa_id": identity.removeprefix("+")},
    }
)


class GroupService:
    def __init__(
        self, conn: Any, capability: CapabilityService, executor: MutationExecutor
    ) -> None:
        self._conn, self._capability, self._executor = conn, capability, executor

    def member(
        self,
        ctx: CallContext,
        tool: str,
        group: str,
        targets: Mapping[str, ProviderTarget],
        recipient: str,
        args: Mapping[str, Any],
        request_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        by_transport = MEMBERSHIP.get(tool)
        if by_transport is None or not targets or not isinstance(args, Mapping):
            raise CommsError("INVALID_ARGUMENT")
        transports = {t.transport for t in targets.values()}
        if len(transports) != 1:
            raise CommsError("INVALID_ARGUMENT")
        (transport,) = transports
        capability = by_transport.get(transport)
        if capability is None:
            raise CommsError("PROVIDER_UNSUPPORTED")
        if set(args) & ({"user_id", "wa_id"} | set(_FIXED.get(tool, {}))):
            raise CommsError("INVALID_ARGUMENT")  # the member is the recipient, never an arg
        identity = member_identity(self._conn, recipient, transport)
        if identity is None:
            raise CommsError("NOT_FOUND")
        chosen = self._capability.choose_actor(targets, capability, preferred=actor)
        target = targets[chosen]
        self._capability.require_for_write(chosen, target, capability)
        op = SemanticOperation(
            capability, {**args, **_FIXED.get(tool, {}), **_MEMBER_ARG[transport](identity)}
        )
        outcome = self._executor.provider(ctx, _tool_name(tool), target, op, request_id)
        if outcome.state == "FAILED":
            self._capability.authoritative(chosen, target, ProviderResult("FAILED", outcome.code))
        return {
            "group": group,
            "recipient": recipient,
            "operation": tool.rsplit(".", 1)[1],
            "result": outcome.state,
            "code": outcome.code,
            "actor": chosen,
            "op_ref": outcome.op_ref,
            "replayed": outcome.replayed,
        }


def _tool_name(tool: str) -> str:
    return "comms_" + tool.replace(".", "_")
