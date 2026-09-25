"""The Bot API ``DeliveryTransport`` (comms v0.3 Task C7; 5b-4 S2, S3; A19, A21).

``normalize``, ``prepare`` and ``still_valid`` are pure (S3). ``prepare`` renders one
``sendMessage`` payload for text content, or ``Skip(CONTENT_UNSUPPORTED)``. The bot has no
customer-service window, so ``still_valid`` is always ``True``. ``deliver`` makes exactly one
``sendMessage`` call and classifies it (A19); the provider ref is ``<marked chat>:<message id>``
(Telegram message ids are unique only per chat); it never retries, because the Bot API has no
idempotency key (A20) and every retry is an explicit Comms decision (A21).
"""

from __future__ import annotations

from datetime import datetime

from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryResult,
    FrozenDelivery,
    PreparedPayload,
    Skip,
)
from comms.transports.telegram.bot.classify import classify_send
from comms.transports.telegram.bot.http import BotApi, BotTransportError
from comms.transports.telegram.message_text import MAX_TEXT, prepare_text, text_payload
from comms.transports.telegram.peers import marked_chat_id

__all__ = ["MAX_TEXT", "BotDelivery"]


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
        return prepare_text(intent)

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        return True

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult:
        chat_id, text = text_payload(delivery.payload, delivery.identity)
        params = {"chat_id": chat_id, "text": text}
        try:
            outcome = self._api.call("sendMessage", params)
        except BotTransportError as exc:
            classified = classify_send(exc)
        else:
            classified = classify_send(outcome)
        ref = classified.provider_message_ref
        return DeliveryResult(
            classified.kind,
            provider_message_ref=f"{delivery.identity}:{ref}" if ref is not None else None,
            retry_after=classified.retry_after,
        )
