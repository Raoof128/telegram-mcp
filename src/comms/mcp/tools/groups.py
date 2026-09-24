"""Group inspection and membership tools (comms v0.3 Task D20; P §24–25, §73).

Inspection reads name groups by ``grp_`` ref and members by what the context engine returns
(roles, never identities). A membership write names the member by recipient ref, is decided
by capability, and reports structured truth: a provider refusal is FAILED with its code, a
direct add the provider refuses for privacy is INVITE_REQUIRED with the group's latest invite.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.mcp.schemas import (
    ACTOR,
    ANY_OBJECT,
    BOOL,
    READ_FAILURES,
    array,
    integer,
    nullable,
    obj,
    read,
    ref,
    string,
    write,
    write_result,
)
from comms.mcp.spec import ToolSpec
from comms.mcp.tools.context import CONTEXT_FAILURES, CURSOR, PAGE

__all__ = ["GROUP_TOOLS"]

_GROUP = ref("group")
_RECIPIENT = ref("recipient")
_INVITE = nullable(ref("invite"))
PERMISSIONS = {
    "type": "object",
    "minProperties": 1,
    "propertyNames": {"pattern": "^can_[a-z_]{1,40}$"},
    "additionalProperties": BOOL,
}
GROUP_VIEW = obj(
    {
        "group": _GROUP,
        "name": string(0, 256),
        "location": ref("location"),
        "enabled": BOOL,
        "created_at": string(1, 64),
    },
    ["group", "name", "location", "enabled", "created_at"],
)
_MEMBER = write_result(recipient=_RECIPIENT)
_ADDED = write_result(recipient=_RECIPIENT, invite=_INVITE)


def _member_write(
    tool: str,
    title: str,
    description: str,
    capability: C,
    extra: Mapping[str, Any] | None = None,
    *,
    required: Sequence[str] = (),
    destructive: bool = False,
    output: Mapping[str, Any] = _MEMBER,
) -> ToolSpec:
    return write(
        f"comms_group_member_{tool}",
        title,
        description,
        f"group.member_{tool}",
        {"group": _GROUP, "recipient": _RECIPIENT, "actor": ACTOR, **(extra or {})},
        ["group", "recipient", *required],
        output,
        capability=capability,
        destructive=destructive,
    )


GROUP_TOOLS: tuple[ToolSpec, ...] = (
    read(
        "comms_group_list",
        "List groups",
        "The groups in the directory, by ref and name, newest first.",
        "group.list",
        {"limit": integer(1, 100), "cursor": string(1, 18)},
        [],
        obj(
            {"items": array(GROUP_VIEW, high=100), "next_cursor": nullable(string(1, 18))},
            ["items", "next_cursor"],
        ),
    ),
    read(
        "comms_group_get",
        "Get a group",
        "One group's directory record: name, location and whether it is enabled.",
        "group.get",
        {"group": _GROUP},
        ["group"],
        GROUP_VIEW,
    ),
    read(
        "comms_group_context",
        "Group context",
        "The group's recent messages, members and admins in one call (as comms_context_get).",
        "group.context",
        {"group": _GROUP, "message_limit": integer(1, 100)},
        ["group"],
        obj(
            {"group_ref": _GROUP, "messages": PAGE, "members": PAGE, "admins": PAGE}, ["group_ref"]
        ),
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    read(
        "comms_group_capabilities",
        "Group capabilities",
        "What each configured actor can do in this group, by capability id (P §35).",
        "group.capabilities",
        {"group": _GROUP},
        ["group"],
        obj({"group_ref": _GROUP, "actors": ANY_OBJECT}, ["group_ref", "actors"]),
        failures=(*READ_FAILURES, "NOT_CONFIGURED", "PROVIDER_UNAVAILABLE"),
        open_world=True,
    ),
    read(
        "comms_group_members_list",
        "List members",
        "The group's members as the provider reports them: role and status, never identities.",
        "group.members_list",
        {"group": _GROUP, "cursor": CURSOR},
        ["group"],
        PAGE,
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    read(
        "comms_group_members_get",
        "Get a member",
        "One recipient's membership of the group. Not offered yet: answers PROVIDER_UNSUPPORTED.",
        "group.members_get",
        {"group": _GROUP, "recipient": _RECIPIENT},
        ["group", "recipient"],
        obj(
            {
                "group": _GROUP,
                "recipient": _RECIPIENT,
                "role": nullable(string(1, 32)),
                "status": nullable(string(1, 32)),
            },
            ["group", "recipient", "role", "status"],
        ),
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    read(
        "comms_group_admins_list",
        "List admins",
        "The group's admins and creator: role and status, never identities.",
        "group.admins_list",
        {"group": _GROUP},
        ["group"],
        PAGE,
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    _member_write(
        "add",
        "Add a member",
        "Add a recipient to the group directly. Never turns into an invite on its own: when "
        "the provider requires one the result is INVITE_REQUIRED with the latest invite.",
        C.MEMBER_ADD,
        output=_ADDED,
    ),
    _member_write(
        "invite",
        "Invite a member",
        "Create a single-use invite for a recipient to join.",
        C.INVITE_CREATE,
        output=_ADDED,
    ),
    _member_write(
        "remove",
        "Remove a member",
        "Remove a recipient from the group; they may rejoin. FAILED with NOT_AUTHORIZED when "
        "the provider refuses, never a false success.",
        C.MEMBER_REMOVE,
        destructive=True,
    ),
    _member_write(
        "ban",
        "Ban a member",
        "Ban a recipient from the group, optionally until a time and deleting their messages.",
        C.MEMBER_BAN,
        {"until_date": integer(0), "revoke_messages": BOOL},
        destructive=True,
    ),
    _member_write("unban", "Unban a member", "Lift a recipient's ban.", C.MEMBER_UNBAN),
    _member_write(
        "restrict",
        "Restrict a member",
        "Set exactly which permissions a recipient keeps; a permission not named is allowed.",
        C.MEMBER_RESTRICT,
        {"permissions": PERMISSIONS, "until_date": integer(0)},
        required=["permissions"],
        destructive=True,
    ),
    _member_write(
        "unrestrict",
        "Lift a restriction",
        "Grant a restricted recipient every permission again.",
        C.MEMBER_RESTRICT,
    ),
)
