"""Rate limits, normalized across providers (comms v0.3 Task C30; A21).

Every provider's throttle — the Bot API's 429, MTProto's ``FLOOD_WAIT_X``, Meta's throttling
codes — reaches core as ``FAILED`` with code ``RATE_LIMITED`` (and ``retry_after`` when the
provider gave one), or, for a send, ``FAILED_TRANSIENT`` with ``retry_after``. ``rate_limit_of``
names that one shape. A throttle is never a success. ``with_rate_limit`` is the only place a
tool call may wait: at most ``MAX_INLINE_WAIT_S``, once, and only for a ``SET_STATE`` operation,
whose repeat is harmless; anything longer or less idempotent is returned with its
``retry_after`` for the caller to decide. No hidden long sleep, no loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from comms.core.delivery.transport import DeliveryResult, ResultKind
from comms.core.providers.protocols import ProviderResult
from comms.core.providers.semantics import OperationSemantics

__all__ = ["MAX_INLINE_WAIT_S", "RateLimited", "rate_limit_of", "with_rate_limit"]

MAX_INLINE_WAIT_S = 2


@dataclass(frozen=True)
class RateLimited:
    retry_after_s: int | None


def rate_limit_of(result: ProviderResult | DeliveryResult) -> RateLimited | None:
    if isinstance(result, DeliveryResult):
        if result.kind is ResultKind.FAILED_TRANSIENT and result.retry_after is not None:
            return RateLimited(result.retry_after)
        return None
    if result.outcome == "FAILED" and result.code == "RATE_LIMITED":
        retry = result.detail.get("retry_after")
        return RateLimited(retry if type(retry) is int and retry > 0 else None)
    return None


def with_rate_limit(
    call: Callable[[], ProviderResult],
    semantics: OperationSemantics,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> ProviderResult:
    result = call()
    limited = rate_limit_of(result)
    if limited is None or semantics.retry_class != "SET_STATE":
        return result
    if limited.retry_after_s is None or limited.retry_after_s > MAX_INLINE_WAIT_S:
        return result
    sleep(limited.retry_after_s)
    return call()
