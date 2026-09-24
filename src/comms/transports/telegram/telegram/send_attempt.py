"""The classified outcome of one MTProto send (comms v0.3 Task C15).

A neutral type: the adapter produces it, ``user/send.py`` reconciles on it, and neither the
reconciler nor its tests need the concrete backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["SendAttempt"]


@dataclass(frozen=True)
class SendAttempt:
    """One send's classified outcome (C15): ``sent`` (with the message id when Telegram gave
    one), ``duplicate`` (RANDOM_ID_DUPLICATE: an earlier copy was accepted), ``refused`` (a
    documented refusal), ``flood``, ``ambiguous`` (a drop, a timeout, a server error), or
    ``failed`` (anything else)."""

    outcome: Literal["sent", "duplicate", "refused", "flood", "ambiguous", "failed"]
    message_id: int | None = None
    retry_after: int | None = None
