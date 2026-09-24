"""The Telegram capability ids (P §11), shared by the bot and user capability providers."""

from __future__ import annotations

from comms.core.providers.capability import Capability
from comms.core.providers.semantics import SUPPORT

__all__ = ["TELEGRAM_CAPABILITIES"]

TELEGRAM_CAPABILITIES = tuple(
    c for c in Capability if set(SUPPORT[c]) & {"telegram_bot", "telegram_user"}
)
