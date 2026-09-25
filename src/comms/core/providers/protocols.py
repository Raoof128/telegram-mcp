"""The provider protocols every adapter implements (comms v0.3 Task C2, A18).

Core names what a provider can do, never how: a capability snapshot per actor and
destination, one ``invoke`` for a semantic operation, a context read, and inbound
normalisation. ``ADAPTER_CONTRACTS`` is A18's machine-readable declaration of which
protocols each adapter implements. A target's transport identity and a result's provider
reference are opaque to logs (``repr=False``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from comms.core.providers.capability import Capability, CapabilityState

__all__ = [
    "ADAPTER_CONTRACTS",
    "AdminOperations",
    "CapabilityProvider",
    "CapabilitySnapshot",
    "ContextPage",
    "ContextQuery",
    "ContextRefused",
    "ContextSource",
    "InboundEvent",
    "InboundSource",
    "ProviderResult",
    "ProviderTarget",
    "SemanticOperation",
]

ADAPTER_CONTRACTS: Mapping[str, frozenset[str]] = {
    "telegram_bot": frozenset({"delivery", "capability", "admin", "context"}),
    "telegram_user": frozenset({"delivery", "capability", "admin", "context"}),
    "whatsapp_cloud": frozenset({"delivery", "capability", "admin"}),
    "whatsapp_webhooks": frozenset({"inbound_context", "provider_updates"}),
}


@dataclass(frozen=True)
class ProviderTarget:
    transport: str
    actor: str  # an ADAPTER_CONTRACTS key
    destination_ref: str
    identity: str = field(repr=False)  # the platform identity (encrypted at rest)


@dataclass(frozen=True)
class CapabilitySnapshot:
    actor: str
    destination_ref: str
    states: Mapping[Capability, CapabilityState]
    observed_at: str


@dataclass(frozen=True)
class SemanticOperation:
    capability: Capability
    args: Mapping[str, Any]


@dataclass(frozen=True)
class ProviderResult:
    outcome: Literal["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"]
    code: str | None
    provider_ref: str | None = field(default=None, repr=False)
    detail: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ContextQuery:
    target: ProviderTarget
    kind: str  # "recent" | "around" | "search" | …
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextPage:
    items: tuple[Mapping[str, Any], ...]
    provenance: str  # telegram_live | telegram_local | whatsapp_webhook_archive | campaign_store
    next_cursor: str | None = None


@dataclass(frozen=True)
class InboundEvent:
    transport: str
    kind: str  # "message" | "status" | …
    provider_event_ref: str
    payload: Mapping[str, Any] = field(repr=False)


class ContextRefused(Exception):
    """A context read the source will not serve (``PROVIDER_UNSUPPORTED``, ``NOT_AUTHORIZED``,
    ``UNAVAILABLE``, …). Fixed message; the code is the contract."""

    def __init__(self, code: str) -> None:
        super().__init__(f"context read refused ({code})")
        self.code = code


class CapabilityProvider(Protocol):
    def snapshot(self, actor: str, destination: ProviderTarget) -> CapabilitySnapshot:
        """Network: never called with a comms.db transaction open."""
        ...


class AdminOperations(Protocol):
    def validate(self, op: SemanticOperation, target: ProviderTarget) -> None:
        """Pure: ``ValueError`` when the operation or its arguments are malformed for this
        actor. The executor calls it before recording, so a refusal leaves no trace."""
        ...

    def invoke(
        self, op: SemanticOperation, target: ProviderTarget, op_key: str
    ) -> ProviderResult: ...


class ContextSource(Protocol):
    def read(self, query: ContextQuery) -> ContextPage: ...


class InboundSource(Protocol):
    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> list[InboundEvent]: ...
