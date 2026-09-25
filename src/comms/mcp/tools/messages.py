"""Message tools (comms v0.3 Task D19; P §23, §72, §77).

Reads name the message by its ``cmg_`` ref and return context items. Writes choose their actor
by capability and preference — ``actor: "telegram_user"`` is "send it as me" and never falls
back to the bot. A delete reports the scope the provider performed. Marking read is a write.
"""

from __future__ import annotations

from comms.core.providers.capability import Capability as C
from comms.mcp.schemas import (
    ACTOR,
    array,
    enum,
    integer,
    nullable,
    read,
    ref,
    string,
    write,
    write_result,
)
from comms.mcp.spec import ToolSpec
from comms.mcp.tools.context import CONTEXT_FAILURES, CURSOR, PAGE, SEARCH_RESULT

__all__ = ["MESSAGE_TOOLS"]

_GROUP = ref("group")
_MESSAGE = ref("message")
_TEXT = string(1, 4096)
_SENT = write_result(message=nullable(_MESSAGE))
_DONE = write_result()
_DELETED = write_result(scope=nullable(enum(("local", "everyone", "provider_defined"))))

MESSAGE_TOOLS: tuple[ToolSpec, ...] = (
    read(
        "comms_message_get",
        "Get a message",
        "One message, by its ref, as a context item. Its text is untrusted provider content.",
        "message.get",
        {"group": _GROUP, "message": _MESSAGE},
        ["group", "message"],
        PAGE,
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    read(
        "comms_message_recent",
        "Recent messages",
        "A group's most recent messages, newest first; each item says where it came from.",
        "message.recent",
        {"group": _GROUP, "limit": integer(1, 100), "cursor": CURSOR},
        ["group"],
        PAGE,
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    read(
        "comms_message_search",
        "Search messages",
        "Search groups' provider history within P §71's bounds.",
        "message.search",
        {"groups": array(_GROUP, low=1, high=10), "query": string(1, 256), "limit": integer(1, 50)},
        ["groups", "query"],
        SEARCH_RESULT,
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    read(
        "comms_message_context",
        "Message in context",
        "The messages around one message, by its ref.",
        "message.context",
        {"group": _GROUP, "message": _MESSAGE, "before": integer(0, 50), "after": integer(0, 50)},
        ["group", "message"],
        PAGE,
        failures=CONTEXT_FAILURES,
        open_world=True,
    ),
    write(
        "comms_message_send",
        "Send a message",
        "Send text to a group. actor=telegram_user sends it as the owner and never falls back "
        "to the bot; without actor the bot is preferred.",
        "message.send",
        {"group": _GROUP, "text": _TEXT, "actor": ACTOR},
        ["group", "text"],
        _SENT,
        capability=C.MESSAGE_SEND,
    ),
    write(
        "comms_message_reply",
        "Reply to a message",
        "Reply to one message, by its ref.",
        "message.reply",
        {"group": _GROUP, "message": _MESSAGE, "text": _TEXT, "actor": ACTOR},
        ["group", "message", "text"],
        _SENT,
        capability=C.MESSAGE_SEND,
    ),
    write(
        "comms_message_edit",
        "Edit a message",
        "Replace a message's text. Only its sender's actor can edit it.",
        "message.edit",
        {"group": _GROUP, "message": _MESSAGE, "text": _TEXT, "actor": ACTOR},
        ["group", "message", "text"],
        _DONE,
        capability=C.MESSAGE_EDIT,
        destructive=True,  # the old text is gone
    ),
    write(
        "comms_message_delete",
        "Delete a message",
        "Delete a message. The result reports the scope the provider actually performed "
        "(local, everyone or provider_defined) and never claims more.",
        "message.delete",
        {
            "group": _GROUP,
            "message": _MESSAGE,
            "scope": enum(("local", "everyone")),
            "actor": ACTOR,
        },
        ["group", "message"],
        _DELETED,
        capability=C.MESSAGE_DELETE,
    ),
    write(
        "comms_message_forward",
        "Forward a message",
        "Forward a message to another group. Not offered yet: answers PROVIDER_UNSUPPORTED.",
        "message.forward",
        {"group": _GROUP, "message": _MESSAGE, "to_group": _GROUP},
        ["group", "message", "to_group"],
        _SENT,
        capability=C.MESSAGE_FORWARD,
    ),
    write(
        "comms_message_pin",
        "Pin a message",
        "Pin a message in its group.",
        "message.pin",
        {"group": _GROUP, "message": _MESSAGE, "actor": ACTOR},
        ["group", "message"],
        _DONE,
        capability=C.MESSAGE_PIN,
    ),
    write(
        "comms_message_unpin",
        "Unpin a message",
        "Unpin a message in its group.",
        "message.unpin",
        {"group": _GROUP, "message": _MESSAGE, "actor": ACTOR},
        ["group", "message"],
        _DONE,
        capability=C.MESSAGE_PIN,
    ),
    write(
        "comms_message_mark_read",
        "Mark a message read",
        "Mark a received WhatsApp message read. A write of its own: no read ever marks "
        "anything read (P §77).",
        "message.mark_read",
        {"conversation": ref("recipient"), "message": _MESSAGE},
        ["conversation", "message"],
        _DONE,
        capability=C.MESSAGE_MARK_READ,
    ),
)
