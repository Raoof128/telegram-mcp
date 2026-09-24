"""The MTProto ``DeliveryTransport`` (comms v0.3 Task C16; A20, A42; 5b-4 S2, S3).

``normalize``, ``prepare`` and ``still_valid`` are pure and shared with the bot transport. The
transport is keyed: ``provider_request_key`` is the job's ``random_id``, which the engine
persists on the attempt before ``deliver`` (A42), so every attempt of a job carries the same
one and Telegram shows at most one message. ``deliver`` refuses without a call when the session
cannot send (revoked, unavailable, not authorised) or the peer is not in the entity cache —
provably unsent, so ``FAILED_TRANSIENT`` — and otherwise runs ``user.send.send`` on the
session's event loop through the injected ``run``.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Protocol

from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryResult,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
    Skip,
)
from comms.transports.telegram.message_text import prepare_text, text_payload
from comms.transports.telegram.peers import marked_chat_id, unmark_chat_id
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.user.send import TextSender, random_id_for, send

__all__ = ["UserDelivery"]

Runner = Callable[[Coroutine[Any, Any, DeliveryResult]], DeliveryResult]
_UNUSABLE = frozenset({"SESSION_REVOKED", "ACCOUNT_UNAVAILABLE", "AUTH_REQUIRED"})


class UserSession(TextSender, Protocol):
    def readiness(self) -> str | None: ...

    def input_peer(self, peer_type: str, peer_id: int) -> Any: ...


class UserDelivery:
    name = "telegram"
    actor = "telegram_user"

    def __init__(self, session: UserSession, *, run: Runner) -> None:
        self._session, self._run = session, run

    def __repr__(self) -> str:
        return "UserDelivery(<redacted>)"

    def normalize(self, platform_identity: str) -> str:
        return marked_chat_id(platform_identity)

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip:
        return prepare_text(intent)

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        return True

    def provider_request_key(self, idempotency_key: str) -> str:
        return str(random_id_for(idempotency_key))

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult:
        _chat_id, text = text_payload(delivery.payload, delivery.identity)
        if self._session.readiness() in _UNUSABLE:
            return DeliveryResult(ResultKind.FAILED_TRANSIENT)
        try:
            peer = self._session.input_peer(*unmark_chat_id(delivery.identity))
        except GatewayError:
            return DeliveryResult(ResultKind.FAILED_TRANSIENT)
        random_id = random_id_for(delivery.idempotency_key)
        return self._run(send(self._session, peer, delivery.identity, text, random_id))
