"""A provider-neutral MTProto update (comms v0.3 Task C21).

The adapter translates raw updates into these; the consumer never sees Telethon. ``message_id``
updates carry the ``random_id`` a send persisted (A42); ``message`` updates carry the marked
chat, the message id and a payload whose text is untrusted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["NeutralUpdate"]


@dataclass(frozen=True)
class NeutralUpdate:
    kind: Literal["message_id", "message"]
    chat: str | None = None
    message_id: int = 0
    random_id: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False)
