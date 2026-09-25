"""Admin, permission, info, invite, join-request, topic and lifecycle tools (comms v0.3 Task
D21; P §26–29, §74).

Promotion takes an explicit rights profile — ``moderator``, ``event_admin``, ``full_admin`` or
``custom`` with exact rights — and never "every permission" by default. Invites and topics are
named by ``inv_`` / ``top_`` refs, never by link or thread id. Every overwrite of group state
is annotated destructive (the Part A list); deleting or migrating a group is destructive and
resolve-only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.mcp.schemas import (
    ACTOR,
    BOOL,
    OUTCOMES,
    READ_FAILURES,
    array,
    enum,
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
from comms.mcp.tools.context import CONTEXT_FAILURES
from comms.mcp.tools.groups import PERMISSIONS

__all__ = ["ADMIN_TOOLS"]

_GROUP = ref("group")
_RECIPIENT = ref("recipient")
_INVITE = ref("invite")
_TOPIC = ref("topic")
PROFILES = ("moderator", "event_admin", "full_admin", "custom")
RIGHTS = {
    "type": "object",
    "minProperties": 1,
    "propertyNames": {"pattern": "^(can_[a-z_]{1,40}|is_anonymous)$"},
    "additionalProperties": BOOL,
}
_MEMBER = write_result(recipient=_RECIPIENT)
_CHANGED = write_result(object=nullable(ref("invite", "topic")))
_INVITE_FIELDS = {
    "name": string(0, 32),
    "expire_date": integer(1),
    "member_limit": integer(1, 99999),
    "creates_join_request": BOOL,
}
_TOPIC_FIELDS = {
    "name": string(1, 128),
    "icon_color": integer(0),
    "icon_custom_emoji_id": string(1, 64),
}
_LISTED = obj(
    {
        "group": _GROUP,
        "items": array({"type": "object"}, high=100),
        "next_cursor": nullable(string(1, 64)),
    },
    ["group", "items", "next_cursor"],
)


def _admin(
    name: str,
    title: str,
    description: str,
    capability: C,
    inputs: Mapping[str, Any],
    required: Sequence[str] = (),
    *,
    destructive: bool = False,
) -> ToolSpec:
    return write(
        f"comms_{name}",
        title,
        description,
        name.replace("_", ".", 1) if name.startswith("group_") else name,
        {"group": _GROUP, "actor": ACTOR, **inputs},
        ["group", *required],
        _CHANGED,
        capability=capability,
        destructive=destructive,
    )


def _member(
    name: str,
    title: str,
    description: str,
    capability: C,
    inputs: Mapping[str, Any] | None = None,
    required: Sequence[str] = (),
    *,
    destructive: bool = False,
) -> ToolSpec:
    return write(
        f"comms_{name}",
        title,
        description,
        name.replace("_", ".", 1),
        {"group": _GROUP, "recipient": _RECIPIENT, "actor": ACTOR, **(inputs or {})},
        ["group", "recipient", *required],
        _MEMBER,
        capability=capability,
        destructive=destructive,
    )


def _list(
    name: str, title: str, description: str, inputs: Mapping[str, Any] | None = None
) -> ToolSpec:
    return read(
        f"comms_{name}",
        title,
        description,
        name.replace("_", ".", 1),
        {"group": _GROUP, "cursor": string(1, 64), **(inputs or {})},
        ["group"],
        _LISTED,
        failures=CONTEXT_FAILURES,
        open_world=True,
    )


ADMIN_TOOLS: tuple[ToolSpec, ...] = (
    # -- admins and permissions (P §26, §74) -------------------------------------------------
    _member(
        "group_admin_promote",
        "Promote to admin",
        "Make a recipient an admin with an explicit rights profile (moderator, event_admin, "
        "full_admin) or custom exact rights. There is no implicit 'every permission'.",
        C.ADMIN_PROMOTE,
        {"profile": enum(PROFILES), "rights": RIGHTS},
        ["profile"],
    ),
    _member(
        "group_admin_update_rights",
        "Change admin rights",
        "Replace an admin's rights with a profile or custom exact rights.",
        C.ADMIN_PROMOTE,
        {"profile": enum(PROFILES), "rights": RIGHTS},
        ["profile"],
        destructive=True,
    ),
    _member(
        "group_admin_demote",
        "Demote an admin",
        "Remove every admin right from a recipient.",
        C.ADMIN_DEMOTE,
        destructive=True,
    ),
    read(
        "comms_group_permissions_get",
        "Default permissions",
        "The group's default member permissions. Not offered yet: answers PROVIDER_UNSUPPORTED.",
        "group.permissions_get",
        {"group": _GROUP},
        ["group"],
        obj({"group": _GROUP, "permissions": PERMISSIONS}, ["group", "permissions"]),
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    _admin(
        "group_permissions_set",
        "Set default permissions",
        "Set the group's default member permissions exactly.",
        C.CHAT_SET_PERMISSIONS,
        {"permissions": PERMISSIONS},
        ["permissions"],
        destructive=True,
    ),
    # -- info (P §26) -----------------------------------------------------------------------
    _admin(
        "group_info_set_title",
        "Set the title",
        "Rename the group.",
        C.CHAT_SET_TITLE,
        {"title": string(1, 128)},
        ["title"],
        destructive=True,
    ),
    _admin(
        "group_info_set_description",
        "Set the description",
        "Replace the group's description.",
        C.CHAT_SET_DESCRIPTION,
        {"description": string(0, 255)},
        ["description"],
        destructive=True,
    ),
    _admin(
        "group_info_set_photo",
        "Set the photo",
        "Replace the group's photo with a media object. Not offered yet: answers "
        "PROVIDER_UNSUPPORTED.",
        C.CHAT_SET_PHOTO,
        {"media": ref("media")},
        ["media"],
        destructive=True,
    ),
    # -- invites and join requests (P §27) ---------------------------------------------------
    _list("group_invite_list", "List invites", "The group's invite links, by ref."),
    _admin(
        "group_invite_create",
        "Create an invite",
        "Create an invite, optionally expiring, limited or needing admin approval. The result "
        "names it by an inv_ ref.",
        C.INVITE_CREATE,
        _INVITE_FIELDS,
    ),
    _admin(
        "group_invite_edit",
        "Edit an invite",
        "Change an invite's expiry, limit, name or approval, by its ref.",
        C.INVITE_EDIT,
        {"invite": _INVITE, **_INVITE_FIELDS},
        ["invite"],
    ),
    _admin(
        "group_invite_revoke",
        "Revoke an invite",
        "Revoke an invite by its ref. On WhatsApp this resets the group's link and returns "
        "the replacement's ref.",
        C.INVITE_REVOKE,
        {"invite": _INVITE},
        destructive=True,
    ),
    _list("group_join_requests_list", "List join requests", "Pending requests to join the group."),
    _member(
        "group_join_requests_approve",
        "Approve a join request",
        "Let a recipient who asked to join in.",
        C.JOIN_REQUEST_APPROVE,
    ),
    _member(
        "group_join_requests_reject",
        "Reject a join request",
        "Turn down a recipient's request to join.",
        C.JOIN_REQUEST_REJECT,
        destructive=True,
    ),
    # -- topics (P §28) -----------------------------------------------------------------------
    _list("group_topic_list", "List topics", "The forum's topics, by ref."),
    read(
        "comms_group_topic_get",
        "Get a topic",
        "One forum topic, by its ref. Not offered yet: answers PROVIDER_UNSUPPORTED.",
        "group.topic_get",
        {"group": _GROUP, "topic": _TOPIC},
        ["group", "topic"],
        obj(
            {"group": _GROUP, "topic": _TOPIC, "name": string(0, 128), "closed": BOOL},
            ["group", "topic", "name", "closed"],
        ),
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    _admin(
        "group_topic_create",
        "Create a topic",
        "Create a forum topic; the result names it by a top_ ref.",
        C.TOPIC_CREATE,
        _TOPIC_FIELDS,
        ["name"],
    ),
    _admin(
        "group_topic_edit",
        "Edit a topic",
        "Rename a topic or change its icon, by its ref.",
        C.TOPIC_EDIT,
        {"topic": _TOPIC, "name": string(1, 128), "icon_custom_emoji_id": string(1, 64)},
        ["topic"],
    ),
    _admin(
        "group_topic_close",
        "Close a topic",
        "Close a topic to new messages.",
        C.TOPIC_CLOSE,
        {"topic": _TOPIC},
        ["topic"],
    ),
    _admin(
        "group_topic_reopen",
        "Reopen a topic",
        "Reopen a closed topic.",
        C.TOPIC_REOPEN,
        {"topic": _TOPIC},
        ["topic"],
    ),
    # -- lifecycle (P §29) --------------------------------------------------------------------
    write(
        "comms_group_create",
        "Create a group",
        "Create a supergroup or channel as the owner's account and register it at a location. "
        "A CREATE: an ambiguous outcome is resolved, never retried.",
        "group.create",
        {
            "location": ref("location"),
            "title": string(1, 128),
            "kind": enum(("supergroup", "broadcast")),
            "about": string(0, 255),
            "forum": BOOL,
            "actor": ACTOR,
        },
        ["location", "title", "kind"],
        obj(
            {
                "result": enum(OUTCOMES),
                "code": nullable(string(1, 64)),
                "actor": nullable(ACTOR),
                "op_ref": nullable(ref("operation")),
                "replayed": BOOL,
                "group": nullable(_GROUP),
            },
            ["result", "code", "actor", "op_ref", "replayed", "group"],
        ),
        capability=C.GROUP_CREATE,
    ),
    _admin(
        "group_delete",
        "Delete the group",
        "Delete the group for everyone. Destructive and resolve-only: an ambiguous outcome is "
        "never retried.",
        C.GROUP_DELETE,
        {},
        destructive=True,
    ),
    _admin(
        "group_migrate",
        "Upgrade to a supergroup",
        "Migrate a basic group to a supergroup; the old chat stops.",
        C.GROUP_MIGRATE,
        {},
        destructive=True,
    ),
    read(
        "comms_group_admin_log",
        "Admin log",
        "The group's recent admin actions: event kinds and times, no content.",
        "group.admin_log",
        {"group": _GROUP, "limit": integer(1, 100), "cursor": string(1, 64)},
        ["group"],
        _LISTED,
        failures=(*READ_FAILURES, "NOT_AUTHORIZED", "PROVIDER_UNSUPPORTED"),
        open_world=True,
    ),
)
