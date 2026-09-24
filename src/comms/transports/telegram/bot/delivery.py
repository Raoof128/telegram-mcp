"""The Bot API ``DeliveryTransport`` (comms v0.3 Task C7; 5b-4 S2, S3; A19, A21).

``normalize``, ``prepare`` and ``still_valid`` are pure (S3). ``prepare`` renders one
``sendMessage`` payload for text content, or ``Skip(CONTENT_UNSUPPORTED)``. The bot has no
customer-service window, so ``still_valid`` is always ``True``. ``deliver`` makes exactly one
``sendMessage`` call and classifies it (A19); it never retries, because the Bot API has no
idempotency key (A20) and every retry is an explicit Comms decision (A21).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from comms.core.canonical import jcs_dumps
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryResult,
    FrozenDelivery,
    PreparedPayload,
    Skip,
    SkipReason,
)
from comms.transports.telegram.bot.classify import classify_send
from comms.transports.telegram.bot.http import BotApi, BotTransportError
from comms.transports.telegram.peers import marked_chat_id

__all__ = ["MAX_TEXT", "BotDelivery"]

MAX_TEXT = 4096  # Telegram's limit, in UTF-16 code units


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class BotDelivery:
    name = "telegram"
    actor = "telegram_bot"

    def __init__(self, api: BotApi) -> None:
        self._api = api

    def __repr__(self) -> str:
        return "BotDelivery(<redacted>)"

    def normalize(self, platform_identity: str) -> str:
        return marked_chat_id(platform_identity)

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip:
        text = _text(intent.content)
        if text is None:
            return Skip(SkipReason.CONTENT_UNSUPPORTED)
        data = jcs_dumps({"chat_id": int(intent.identity), "text": text})
        return PreparedPayload(data=data, digest=hashlib.sha256(data).hexdigest())

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        return True

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult:
        params = json.loads(delivery.payload.data)
        if str(params["chat_id"]) != delivery.identity:
            raise ValueError("the payload is not for this delivery")
        try:
            outcome = self._api.call("sendMessage", params)
        except BotTransportError as exc:
            classified = classify_send(exc)
        else:
            classified = classify_send(outcome)
        return DeliveryResult(
            classified.kind,
            provider_message_ref=classified.provider_message_ref,
            retry_after=classified.retry_after,
        )


def _text(content: Mapping[str, Any]) -> str | None:
    text = content.get("text") if set(content) == {"text"} else None
    if not isinstance(text, str) or not text or _utf16_len(text) > MAX_TEXT:
        return None
    return text
