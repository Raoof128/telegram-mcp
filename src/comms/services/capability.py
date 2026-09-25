"""The capability service (comms v0.3 Task D6; P §9, §35, §68; A25).

Snapshots are cached per actor and destination with the time they were taken, and refreshed
when stale and before every consequential write. ``choose_actor`` follows P §68: the
destination's configured actor, then the required capability, then an explicit preference (an
explicit actor that cannot do it is refused, never swapped), then the actor with the least extra
authority (the bot before the user account). ``UNKNOWN`` is never available (P §9). The
provider's answer to a write is final (A25): a refusal drops the cached snapshot and its code is
the caller's answer. Network: never called inside a comms.db transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from comms.core.errors import CommsError
from comms.core.providers.capability import Capability
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import (
    CapabilityProvider,
    CapabilitySnapshot,
    ProviderResult,
    ProviderTarget,
)
from comms.core.providers.semantics import SUPPORT

__all__ = ["AUTHORITY_ORDER", "STATE_CODE", "CapabilityService"]

# P §68 step 4: least extra authority first.
AUTHORITY_ORDER = ("telegram_bot", "whatsapp_cloud", "telegram_user")
STATE_CODE = {
    S.NOT_AUTHORIZED: "NOT_AUTHORIZED",
    S.ACCOUNT_INELIGIBLE: "ACCOUNT_INELIGIBLE",
    S.PROVIDER_UNSUPPORTED: "PROVIDER_UNSUPPORTED",
    S.NOT_CONFIGURED: "NOT_CONFIGURED",
    S.TEMPORARILY_UNAVAILABLE: "PROVIDER_UNAVAILABLE",
    S.UNAVAILABLE: "CAPABILITY_UNAVAILABLE",
    S.UNKNOWN: "CAPABILITY_UNAVAILABLE",
}
# When no actor can act, the most actionable reason is reported first.
_REASON_ORDER = (
    "NOT_AUTHORIZED",
    "ACCOUNT_INELIGIBLE",
    "PROVIDER_UNAVAILABLE",
    "CAPABILITY_UNAVAILABLE",
    "NOT_CONFIGURED",
    "PROVIDER_UNSUPPORTED",
)


class CapabilityService:
    def __init__(
        self,
        providers: Mapping[str, CapabilityProvider],
        *,
        clock: Callable[[], datetime],
        max_age: timedelta = timedelta(minutes=5),
    ) -> None:
        self._providers, self._clock, self._max_age = dict(providers), clock, max_age
        self._cache: dict[tuple[str, str], tuple[datetime, CapabilitySnapshot]] = {}

    def snapshot(
        self, actor: str, target: ProviderTarget, *, refresh: bool = False
    ) -> CapabilitySnapshot:
        provider = self._providers.get(actor)
        if provider is None:
            raise CommsError("NOT_CONFIGURED")
        key = (actor, target.destination_ref)
        cached = self._cache.get(key)
        now = self._clock()
        if refresh or cached is None or now - cached[0] > self._max_age:
            cached = (now, provider.snapshot(actor, target))
            self._cache[key] = cached
        return cached[1]

    def state(
        self, actor: str, target: ProviderTarget, capability: Capability, *, refresh: bool = False
    ) -> S:
        return self.snapshot(actor, target, refresh=refresh).states.get(capability, S.UNKNOWN)

    def require_for_write(self, actor: str, target: ProviderTarget, capability: Capability) -> None:
        """A consequential write: a fresh snapshot, and only an explicit AVAILABLE passes."""
        state = self.state(actor, target, capability, refresh=True)
        if state is not S.AVAILABLE:
            raise CommsError(STATE_CODE[state])

    def choose_actor(
        self,
        targets: Mapping[str, ProviderTarget],
        capability: Capability,
        *,
        configured: str | None = None,
        preferred: str | None = None,
    ) -> str:
        actors = (
            [configured] if configured is not None else [a for a in AUTHORITY_ORDER if a in targets]
        )
        if preferred is not None and preferred != "auto":
            if preferred not in actors:
                raise CommsError("CAPABILITY_UNAVAILABLE")
            actors = [preferred]
        reasons = []
        for actor in actors:
            state = self.state(actor, targets[actor], capability)
            if state is S.AVAILABLE:
                return actor
            reasons.append(STATE_CODE[state])
        best = min(reasons, key=_REASON_ORDER.index) if reasons else "CAPABILITY_UNAVAILABLE"
        raise CommsError(best)

    def authoritative(
        self, actor: str, target: ProviderTarget, result: ProviderResult
    ) -> ProviderResult | None:
        """The provider's verdict on a write; a refusal also drops the cached snapshot (A25)."""
        if result.outcome == "SUCCEEDED":
            return None
        if result.outcome == "FAILED":
            self._cache.pop((actor, target.destination_ref), None)
        return result

    def for_group(
        self, group_ref: str, targets: Mapping[str, ProviderTarget], *, refresh: bool = False
    ) -> dict[str, Any]:
        """P §35's shape: each actor's available-or-not states, by capability id."""
        actors = {}
        for actor in sorted(targets):
            states = self.snapshot(actor, targets[actor], refresh=refresh).states
            actors[actor] = {cap.value: state.value for cap, state in states.items()}
        return {"group_ref": group_ref, "actors": actors}

    def refresh(self, group_ref: str, targets: Mapping[str, ProviderTarget]) -> dict[str, Any]:
        """``capability.refresh``: the group's matrix from fresh snapshots."""
        return self.for_group(group_ref, targets, refresh=True)

    def get(
        self, group_ref: str, actor: str, target: ProviderTarget, capability: Capability
    ) -> dict[str, Any]:
        """``capability.get``: one actor's state for one capability at one group."""
        return {
            "group_ref": group_ref,
            "actor": actor,
            "capability": capability.value,
            "state": self.state(actor, target, capability).value,
        }

    def for_actor(self, actor: str, groups: Sequence[tuple[str, ProviderTarget]]) -> dict[str, Any]:
        """``capability.for_actor``: one actor's states across groups."""
        return {
            "actor": actor,
            "groups": {
                group: {c.value: s.value for c, s in self.snapshot(actor, t).states.items()}
                for group, t in groups
            },
        }

    def list(self) -> dict[str, list[str]]:
        """``capability.list``: every capability id each configured actor can ever offer."""
        return {
            actor: sorted(c.value for c, actors in SUPPORT.items() if actor in actors)
            for actor in sorted(self._providers)
        }
