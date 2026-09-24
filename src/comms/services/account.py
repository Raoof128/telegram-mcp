"""Account services (comms v0.3 Task D17; P §34): why an operation is or is not possible.

``status`` summarises each actor's capability snapshot: whether it is configured, how many
capabilities are available, and the reasons for the rest, counted by state. It names no
account identity; that is ``admin.identity.inspect``'s alone. ``webhook_status`` reports
whether the webhook ingress is configured and the inbox's pending and completed counts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget
from comms.services.capability import CapabilityService

__all__ = ["AccountService", "WebhookState"]


class WebhookState(Protocol):
    def counts(self) -> Mapping[str, Any]: ...


class AccountService:
    def __init__(self, capability: CapabilityService, *, webhooks: WebhookState) -> None:
        self._capability, self._webhooks = capability, webhooks

    def status(self, targets: Mapping[str, ProviderTarget]) -> dict[str, Any]:
        actors = {}
        for actor in sorted(targets):
            states = self._capability.snapshot(actor, targets[actor], refresh=True).states
            reasons: dict[str, int] = {}
            for state in states.values():
                if state is not S.AVAILABLE:
                    reasons[state.value] = reasons.get(state.value, 0) + 1
            actors[actor] = {
                "configured": S.NOT_CONFIGURED.value not in reasons
                or reasons[S.NOT_CONFIGURED.value] < len(states),
                "available": sum(state is S.AVAILABLE for state in states.values()),
                "reasons": reasons,
            }
        return {"actors": actors}

    def webhook_status(self) -> dict[str, Any]:
        counts = self._webhooks.counts()
        return {
            "configured": bool(counts.get("configured")),
            "pending": int(counts.get("pending", 0)),
            "completed": int(counts.get("completed", 0)),
        }
