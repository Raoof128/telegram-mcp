"""MTProto message writes (comms v0.3 Task D14; P §23, §72): send, edit, delete, pin.

``message.send`` is keyed: its ``random_id`` comes from the operation key (A20), so a retry
with the same key is one message on Telegram, and it runs through ``user.send`` with its one
reconciliation. A delete asks for everyone unless ``revoke`` is false; a supergroup or channel
delete is always for everyone, and the adapter reports the scope it actually performed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram.args import boolean, positive_int, take, text

__all__ = ["MESSAGE_SPECS", "TEXT_MAX"]

TEXT_MAX = 4096


def _send(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {"text": text(1, TEXT_MAX)}, {"reply_to_message_id": positive_int})


def _edit(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {"message_id": positive_int, "text": text(1, TEXT_MAX)}, {})


def _delete(args: Mapping[str, Any]) -> dict[str, Any]:
    fields = take(args, {"message_id": positive_int}, {"revoke": boolean})
    return {"message_id": fields["message_id"], "revoke": fields.get("revoke", True)}


def _pin(args: Mapping[str, Any]) -> dict[str, Any]:
    return take(args, {"message_id": positive_int, "pinned": boolean}, {})


MESSAGE_SPECS = {
    C.MESSAGE_SEND: _send,
    C.MESSAGE_EDIT: _edit,
    C.MESSAGE_DELETE: _delete,
    C.MESSAGE_PIN: _pin,
}
