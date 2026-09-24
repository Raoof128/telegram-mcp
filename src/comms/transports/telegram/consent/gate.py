"""DisclosureGate seam: protocol + synthetic Phase-2 implementation.

Phase-2 flow ends at consent-verified. Budget reservation belongs to
Phase 3: this module defines the ``DisclosureGate`` protocol consumed by
the vertical slice, plus ``SyntheticDisclosureGate`` which always allows
with zero accounting. Phase 3 replaces the synthetic with the
exposure-budget, receipt, and audit-chain services — never by editing
this seam's protocol shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from comms.transports.telegram.consent.broker import ConsumedChallenge

__all__ = [
    "DisclosureDecision",
    "DisclosureGate",
    "SyntheticDisclosureGate",
]


@dataclass(frozen=True)
class DisclosureDecision:
    """Gate verdict: allow/deny for one consumed challenge."""

    allowed: bool


@runtime_checkable
class DisclosureGate(Protocol):
    """Authority seam between consent-verified challenges and retrieval."""

    async def authorize_disclosure(
        self, *, challenge: ConsumedChallenge | None, snapshot: Any | None
    ) -> DisclosureDecision:
        """Return the disclosure verdict for a consumed challenge."""
        ...  # pragma: no cover - protocol shape only


class SyntheticDisclosureGate:
    """Phase-2 synthetic gate: always allow, zero accounting.

    Not the Phase-3 seam. ``authorize_disclosure`` returns a boolean, so it
    cannot release a payload; the Phase-3 coordinator owns retrieval through
    anchor refresh and returns the payload with its receipt. This class has
    no production caller and survives Phase 3 only as a test double.

    ``accounted_records`` is a hard-zero property (not a counter) so the
    synthetic form cannot accumulate disclosure accounting by accident.
    """

    @property
    def accounted_records(self) -> int:
        """Disclosed-record tally: always 0 for the synthetic gate."""
        return 0

    async def authorize_disclosure(
        self, *, challenge: ConsumedChallenge | None, snapshot: Any | None
    ) -> DisclosureDecision:
        """Allow unconditionally; record nothing."""
        return DisclosureDecision(allowed=True)
