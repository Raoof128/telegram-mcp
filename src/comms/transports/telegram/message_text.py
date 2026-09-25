"""Telegram message text, shared by the bot and user transports (comms v0.3 C7, C16).

The message is the campaign's rendered text (``core.campaigns.render``), carried when it is
1..4096 UTF-16 code units (Telegram counts code units); anything else is not carried.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from comms.core.campaigns.render import rendered_text
from comms.core.canonical import jcs_dumps
from comms.core.delivery.transport import DeliveryIntent, PreparedPayload, Skip, SkipReason

__all__ = ["MAX_TEXT", "message_text", "prepare_text", "text_payload"]

MAX_TEXT = 4096


def message_text(content: Mapping[str, Any]) -> str | None:
    text = rendered_text(content)
    if text is None or len(text.encode("utf-16-le")) // 2 > MAX_TEXT:
        return None
    return text


def prepare_text(intent: DeliveryIntent) -> PreparedPayload | Skip:
    """Pure (S3): one ``{"chat_id", "text"}`` payload for the marked chat, or a Skip."""
    text = message_text(intent.content)
    if text is None:
        return Skip(SkipReason.CONTENT_UNSUPPORTED)
    data = jcs_dumps({"chat_id": int(intent.identity), "text": text})
    return PreparedPayload(data=data, digest=hashlib.sha256(data).hexdigest())


def text_payload(payload: PreparedPayload, identity: str) -> tuple[int, str]:
    """The payload's chat and text; refused when it is not for the frozen identity."""
    params = json.loads(payload.data)
    if str(params["chat_id"]) != identity:
        raise ValueError("the payload is not for this delivery")
    return int(params["chat_id"]), str(params["text"])
