"""MTProto sends deduplicated by ``random_id``, reconciled once (comms v0.3 Task C15; A20, A42).

``random_id_for`` derives the send's ``random_id`` from the job's idempotency key, so every
attempt of a job carries the same one and Telegram shows at most one message for it. ``send``
makes one send through the adapter (``send_text_once``); on an ambiguous outcome (a drop, a
timeout, a server error) it reissues the identical request once, inside
``RECONCILE_WINDOW_S``. ``RANDOM_ID_DUPLICATE`` proves an earlier copy was accepted. After any
ambiguity the result is ``ACCEPTED`` or ``OUTCOME_UNKNOWN``; before it, ``FLOOD_WAIT`` is
``FAILED_TRANSIENT`` and a documented refusal ``FAILED_PERMANENT``. ``correlate_message_id``
binds a late ``updateMessageID`` to its attempt through the key persisted before the call.
Only the adapter imports Telethon; this module is pure.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from comms.core import domains
from comms.core.delivery.reducer import bind_provider_ref
from comms.core.delivery.transport import DeliveryResult, ResultKind
from comms.transports.telegram.telegram.send_attempt import SendAttempt

__all__ = ["RECONCILE_WINDOW_S", "correlate_message_id", "random_id_for", "send"]

RECONCILE_WINDOW_S = 10.0
ACTOR = "telegram_user"


class TextSender(Protocol):
    async def send_text_once(
        self, peer: Any, text: str, random_id: int, *, timeout: float, reply_to: int | None = None
    ) -> SendAttempt: ...


def random_id_for(idempotency_key: str) -> int:
    """``signed_nonzero_u64(SHA256("comms-mtproto-random-id/v1\\0" ‖ key))`` (A20)."""
    digest = hashlib.sha256(domains.MTPROTO_RANDOM_ID + bytes.fromhex(idempotency_key)).digest()
    return int.from_bytes(digest[:8], "big", signed=True) or 1


def _accepted(chat: str, message_id: int | None) -> DeliveryResult:
    ref = f"{chat}:{message_id}" if message_id is not None else None
    return DeliveryResult(ResultKind.ACCEPTED, provider_message_ref=ref)


async def send(
    session: TextSender,
    peer: Any,
    chat: str,
    text: str,
    random_id: int,
    *,
    clock: Callable[[], float] = time.monotonic,
    reply_to: int | None = None,
) -> DeliveryResult:
    started = clock()

    async def attempt() -> SendAttempt:
        remaining = RECONCILE_WINDOW_S - (clock() - started)
        return await session.send_text_once(
            peer, text, random_id, timeout=remaining, reply_to=reply_to
        )

    first = await attempt()
    if first.outcome in ("sent", "duplicate"):
        return _accepted(chat, first.message_id)
    if first.outcome == "refused":
        return DeliveryResult(ResultKind.FAILED_PERMANENT)
    if first.outcome == "flood":
        return DeliveryResult(ResultKind.FAILED_TRANSIENT, retry_after=first.retry_after)
    if first.outcome != "ambiguous" or clock() - started >= RECONCILE_WINDOW_S:
        return DeliveryResult(ResultKind.OUTCOME_UNKNOWN)
    second = await attempt()  # the one identical reissue: Telegram dedupes by random_id
    if second.outcome in ("sent", "duplicate"):
        return _accepted(chat, second.message_id)
    return DeliveryResult(ResultKind.OUTCOME_UNKNOWN)


def correlate_message_id(conn: Any, random_id: int, message_id: int, *, now: datetime) -> bool:
    """Bind a late ``updateMessageID`` to the attempt that persisted its ``random_id``.

    Runs inside the caller's transaction. False when no attempt holds the key or the attempt
    already has its ref.
    """
    row = conn.execute(
        "SELECT a.id, a.provider_message_ref, i.identity FROM delivery_attempts a"
        " JOIN delivery_jobs j ON j.id = a.job_id JOIN delivery_identities i ON i.id = j.identity_id"
        " WHERE a.transport_actor = ? AND a.provider_request_key = ?"
        " ORDER BY a.attempt_no DESC LIMIT 1",
        (ACTOR, str(random_id)),
    ).fetchone()
    if row is None or row[1] is not None:
        return False
    bind_provider_ref(conn, row[0], f"{row[2]}:{message_id}", now=now)
    return True
