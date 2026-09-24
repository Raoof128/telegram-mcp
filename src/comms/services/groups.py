"""Group admin services (comms v0.3 Tasks D12–D13; P §25–29, §73, §74).

Every write goes one way: the tool names one capability per transport (a tool a transport cannot
perform is ``PROVIDER_UNSUPPORTED``, never a different operation, P §25); the actor is chosen
by capability and preference; a fresh snapshot must say ``AVAILABLE``; and the call runs through
``MutationExecutor.provider`` with its semantics, replay and audit. The result is structured
truth (P §73): a provider refusal is ``FAILED`` with its code, never success.

``member`` acts on one person, named by recipient ref and resolved to that recipient's identity
on the group's transport. ``admin`` acts on the group: its info, permissions, invites, topics
and lifecycle. Provider objects are named by ref — a created invite or topic comes back as an
``inv_`` / ``top_`` ref, and later calls take the ref, never the link or thread id. Promotion
takes an explicit rights profile (P §74), which the adapter validates before anything is
recorded. A direct add the provider refuses for privacy is ``INVITE_REQUIRED`` with the group's
latest invite object, and is never turned into an invite on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from comms.core.campaigns.directory import destination_id, has_recipient, member_identity
from comms.core.errors import CommsError
from comms.core.objects import latest_object
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget
from comms.services.capability import CapabilityService
from comms.services.mutations import CallContext, MutationExecutor
from comms.services.writes import ProviderWrites, summary, transport_of

__all__ = ["ADMIN", "MEMBERSHIP", "GroupService"]

# tool → the capability that performs it on each transport (P §25).
MEMBERSHIP: Mapping[str, Mapping[str, C]] = MappingProxyType(
    {
        "group.member.add": {"telegram": C.MEMBER_ADD},
        "group.member.invite": {"telegram": C.INVITE_CREATE},
        "group.member.remove": {"telegram": C.MEMBER_REMOVE, "whatsapp": C.GROUP_MEMBER_REMOVE},
        "group.member.ban": {"telegram": C.MEMBER_BAN},
        "group.member.unban": {"telegram": C.MEMBER_UNBAN},
        "group.member.restrict": {"telegram": C.MEMBER_RESTRICT},
        "group.member.unrestrict": {"telegram": C.MEMBER_RESTRICT},
        "group.admin.promote": {"telegram": C.ADMIN_PROMOTE},
        "group.admin.demote": {"telegram": C.ADMIN_DEMOTE},
        "group.admin.update_rights": {"telegram": C.ADMIN_PROMOTE},
        "group.join_requests.approve": {"telegram": C.JOIN_REQUEST_APPROVE},
        "group.join_requests.reject": {"telegram": C.JOIN_REQUEST_REJECT},
    }
)
# tool → capability per transport (P §26–29). Hiding a topic has no capability id (C11).
ADMIN: Mapping[str, Mapping[str, C]] = MappingProxyType(
    {
        "group.info.set_title": {"telegram": C.CHAT_SET_TITLE, "whatsapp": C.GROUP_SETTINGS_UPDATE},
        "group.info.set_description": {
            "telegram": C.CHAT_SET_DESCRIPTION,
            "whatsapp": C.GROUP_SETTINGS_UPDATE,
        },
        "group.info.set_photo": {"telegram": C.CHAT_SET_PHOTO},
        "group.permissions.set": {"telegram": C.CHAT_SET_PERMISSIONS},
        "group.invite.create": {"telegram": C.INVITE_CREATE},
        "group.invite.edit": {"telegram": C.INVITE_EDIT},
        "group.invite.revoke": {"telegram": C.INVITE_REVOKE, "whatsapp": C.GROUP_INVITE_RESET},
        "group.topic.create": {"telegram": C.TOPIC_CREATE},
        "group.topic.edit": {"telegram": C.TOPIC_EDIT},
        "group.topic.close": {"telegram": C.TOPIC_CLOSE},
        "group.topic.reopen": {"telegram": C.TOPIC_REOPEN},
        "group.topic.hide": {},
        "group.topic.unhide": {},
        "group.delete": {"telegram": C.GROUP_DELETE},
        "group.migrate": {"telegram": C.GROUP_MIGRATE},
    }
)
# What a CREATE makes; the executor turns its provider ref into an object ref.
_CREATES: Mapping[C, str] = MappingProxyType(
    {C.INVITE_CREATE: "invite", C.TOPIC_CREATE: "topic", C.GROUP_INVITE_RESET: "invite"}
)
# Arguments a tool fixes itself, never taken from the caller.
_FIXED: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {"group.member.unrestrict": {"permissions": "all"}, "group.member.invite": {"member_limit": 1}}
)
# Tools that act for a person without naming them to the provider.
_NO_MEMBER_ARG = frozenset({"group.member.invite"})
# A transport that can only invite, never add directly (P §25).
_INVITE_ONLY = frozenset({"whatsapp"})
# Neutral argument names a transport spells differently.
_RENAMED: Mapping[tuple[str, str], Mapping[str, str]] = MappingProxyType(
    {("group.info.set_title", "whatsapp"): {"title": "subject"}}
)
# A caller's object argument → (object kind, the provider field its identity fills).
_OBJECT_ARGS = {"invite": ("invite", "invite_link"), "topic": ("topic", "message_thread_id")}


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
        self._conn, self._writes = conn, ProviderWrites(conn, capability, executor)

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
        transport = transport_of(targets)
        if set(args) & ({"user_id", "wa_id"} | set(_FIXED.get(tool, {}))):
            raise CommsError("INVALID_ARGUMENT")  # the member is the recipient, never an arg
        head = {"group": group, "recipient": recipient, "operation": tool.rsplit(".", 1)[1]}
        if tool == "group.member.add" and transport in _INVITE_ONLY:
            return {
                **head,
                "result": "INVITE_REQUIRED",
                "code": None,
                "actor": None,
                "op_ref": None,
                "replayed": False,
                "invite": None,
            }
        capability = by_transport.get(transport)
        if capability is None:
            raise CommsError("PROVIDER_UNSUPPORTED")
        if tool in _NO_MEMBER_ARG:
            if not has_recipient(self._conn, recipient):
                raise CommsError("NOT_FOUND")
            member: dict[str, Any] = {}
        else:
            identity = member_identity(self._conn, recipient, transport)
            if identity is None:
                raise CommsError("NOT_FOUND")
            member = _MEMBER_ARG[transport](identity)
        call = {**args, **_FIXED.get(tool, {}), **member}
        chosen, target, outcome = self._writes.write(
            ctx,
            tool,
            targets,
            capability,
            call,
            request_id,
            actor,
            object_kind=_CREATES.get(capability),
        )
        result = {**head, **summary(chosen, outcome)}
        if tool == "group.member.invite":
            result["invite"] = outcome.result.get("object_ref")
        if tool == "group.member.add":
            result["invite"] = None
            if outcome.state == "FAILED" and outcome.code == "INVITE_REQUIRED":
                result.update(result="INVITE_REQUIRED", code=None, invite=self._invite(target))
        return result

    def admin(
        self,
        ctx: CallContext,
        tool: str,
        group: str,
        targets: Mapping[str, ProviderTarget],
        args: Mapping[str, Any],
        request_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        by_transport = ADMIN.get(tool)
        if by_transport is None or not targets or not isinstance(args, Mapping):
            raise CommsError("INVALID_ARGUMENT")
        transport = transport_of(targets)
        capability = by_transport.get(transport)
        if capability is None:
            raise CommsError("PROVIDER_UNSUPPORTED")
        renamed = _RENAMED.get((tool, transport), {})
        call = {renamed.get(k, k): v for k, v in args.items() if k not in _OBJECT_ARGS}
        objects = {_OBJECT_ARGS[k]: v for k, v in args.items() if k in _OBJECT_ARGS}
        chosen, _target, outcome = self._writes.write(
            ctx,
            tool,
            targets,
            capability,
            call,
            request_id,
            actor,
            objects=objects,
            object_kind=_CREATES.get(capability),
        )
        return {
            "group": group,
            "operation": tool,
            **summary(chosen, outcome),
            "object": outcome.result.get("object_ref"),
        }

    def _invite(self, target: ProviderTarget) -> str | None:
        where = destination_id(self._conn, target.destination_ref)
        return latest_object(self._conn, "invite", target.transport, where)
