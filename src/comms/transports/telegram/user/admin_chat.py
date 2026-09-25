"""MTProto chat info, invites, join requests, topics and group lifecycle, validated
(comms v0.3 Task C19; P §26–29; A27, O7).

Pure: each entry returns the checked spec the adapter builds its one RPC from (the rules are
``chat_specs``, shared with the bot). ``group.delete`` is destructive and resolve-only (O7);
``group.migrate`` applies to basic groups only. ``group.create`` has no destination and is
``UserAdmin.create_group``. Setting the chat photo needs an upload and comes with the Part D
media path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram import chat_specs as specs
from comms.transports.telegram.args import boolean, take, text

__all__ = ["CHAT_SPECS", "group_create"]


def _nothing(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {}, {})


def group_create(args: Mapping[str, Any]) -> dict[str, Any]:
    fields = take(
        args,
        {"title": text(1, 128), "kind": lambda v: v in ("supergroup", "broadcast")},
        {"about": text(0, 255), "forum": boolean},
    )
    if fields.get("forum") and fields["kind"] != "supergroup":
        raise ValueError("only a supergroup can be a forum")
    return fields


CHAT_SPECS = {
    C.CHAT_SET_TITLE: specs.set_title,
    C.CHAT_SET_DESCRIPTION: specs.set_description,
    C.CHAT_SET_PERMISSIONS: specs.set_permissions,
    C.INVITE_CREATE: specs.invite_create,
    C.INVITE_EDIT: specs.invite_edit,
    C.INVITE_REVOKE: specs.invite_revoke,
    C.JOIN_REQUEST_APPROVE: specs.member,
    C.JOIN_REQUEST_REJECT: specs.member,
    C.TOPIC_CREATE: specs.topic_create,
    C.TOPIC_EDIT: specs.topic_edit,
    C.TOPIC_CLOSE: specs.thread,
    C.TOPIC_REOPEN: specs.thread,
    C.GROUP_DELETE: _nothing,
    C.GROUP_MIGRATE: _nothing,
}
