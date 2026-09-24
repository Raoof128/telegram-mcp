"""Bot API message writes (comms v0.3 Task D14; P §23, §72): send, edit, delete.

``message.send`` is a ``MESSAGE_SEND`` without a provider key (resolve-only on ambiguity); its
provider ref is the new ``message_id``. The bot cannot delete a message only for itself, so a
local delete is not performed here, and a successful delete is reported as ``everyone``.
Pinning is in ``admin_chat``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram.args import boolean, positive_int, take, text

__all__ = ["DELETE_SCOPE", "MESSAGE_REQUESTS"]

DELETE_SCOPE = "everyone"
TEXT_MAX = 4096


def _send(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"text": text(1, TEXT_MAX)}, {"reply_to_message_id": positive_int})
    params: dict[str, Any] = {"chat_id": chat_id, "text": fields["text"]}
    if "reply_to_message_id" in fields:
        params["reply_parameters"] = {"message_id": fields["reply_to_message_id"]}
    return "sendMessage", params


def _edit(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"message_id": positive_int, "text": text(1, TEXT_MAX)}, {})
    return "editMessageText", {"chat_id": chat_id, **fields}


def _delete(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = take(args, {"message_id": positive_int}, {"revoke": boolean})
    if fields.get("revoke") is False:
        raise NotImplementedError("the bot cannot delete a message only for itself")
    return "deleteMessage", {"chat_id": chat_id, "message_id": fields["message_id"]}


MESSAGE_REQUESTS = {C.MESSAGE_SEND: _send, C.MESSAGE_EDIT: _edit, C.MESSAGE_DELETE: _delete}
