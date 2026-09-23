"""The one gateway-side error the Telegram layer raises (no Telethon import)."""

from __future__ import annotations

__all__ = ["GatewayError"]


class GatewayError(Exception):
    """A frozen §27.1 code, plus ``retry_after`` for FLOOD_WAIT."""

    def __init__(self, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
