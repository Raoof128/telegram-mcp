"""The transport adapter contract, the 5d boundary (design §4, §7.2, S3).

Core never imports a transport. A transport is handed to core as an object
satisfying :class:`DeliveryTransport`. Identity, content and payload fields
are excluded from ``repr`` so an accidental log line or traceback never prints them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DeliveryIntent",
    "DeliveryResult",
    "DeliveryTransport",
    "FrozenDelivery",
    "PreparedPayload",
    "ResultKind",
    "Skip",
    "SkipReason",
]


class ResultKind(StrEnum):
    """What ``deliver`` may report (design §7.2). 5d's adapter conformance tests enforce each meaning.

    ACCEPTED / DELIVERED: the provider took / delivered the message.
    FAILED_TRANSIENT: ONLY when (a) the adapter can prove no provider-side send occurred,
        or (b) the provider's idempotency makes resending this exact FrozenDelivery under
        this idempotency key safe.
    FAILED_PERMANENT: the provider definitively refused.
    OUTCOME_UNKNOWN: everything ambiguous — timeouts, connection loss after writing,
        ambiguous responses, uncertain acknowledgement, and any exception.
    """

    ACCEPTED = "ACCEPTED"
    DELIVERED = "DELIVERED"
    FAILED_TRANSIENT = "FAILED_TRANSIENT"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class SkipReason(StrEnum):
    PLATFORM_INELIGIBLE = "platform_ineligible"
    CONTENT_UNSUPPORTED = "content_unsupported"  # the transport cannot carry this content
    TEMPLATE_REQUIRED = "template_required"  # outside the free-form window, and no template


@dataclass(frozen=True)
class DeliveryIntent:
    transport: str
    identity: str = field(repr=False)
    content: Mapping[str, Any] = field(repr=False)
    # Local facts the freeze copies in from comms.db inside its transaction, so prepare stays
    # pure (S3): the mirrored customer-service window, the campaign's template binding (A22, A23).
    facts: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class PreparedPayload:
    data: bytes = field(repr=False)
    digest: str  # sha256(data).hexdigest()


@dataclass(frozen=True)
class Skip:
    reason: SkipReason


@dataclass(frozen=True)
class FrozenDelivery:
    job_ref: str
    generation_ref: str
    transport: str
    identity: str = field(repr=False)
    payload: PreparedPayload = field(repr=False)
    idempotency_key: str = field(repr=False)
    attempt_no: int = 1


@dataclass(frozen=True)
class DeliveryResult:
    kind: ResultKind
    provider_message_ref: str | None = None
    retry_after: int | None = None  # seconds; a provider's documented back-off (FAILED_TRANSIENT)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ResultKind(self.kind))


@runtime_checkable
class DeliveryTransport(Protocol):
    """A delivery transport (design §4).

    ``normalize``, ``prepare`` and ``still_valid`` perform no I/O of any kind — no
    network, no filesystem read or write, no other database, no RPC, no subprocess,
    no await — and are deterministic for their inputs; they run inside comms.db
    transactions (S3). Anything needing a live provider check belongs in ``deliver``.
    ``deliver`` is the only external side-effect boundary and is called with no
    transaction open.
    """

    name: str

    def normalize(self, platform_identity: str) -> str: ...

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip: ...

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str: ...

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult: ...
