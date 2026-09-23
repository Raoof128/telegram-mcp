"""In-memory ``tgl_`` selection handles for operator discovery (spec §10.2).

Handles live 5 minutes, are bound to one snapshot and to the policy epoch at
discovery, are never written to SQLite or logs, and die with the process.
They contain no reversible Telegram id.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["DiscoveryStore"]

_TTL_S = 300.0


class DiscoveryStore:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._handles: dict[str, tuple[Any, int, float]] = {}

    def invalidate_all(self) -> None:
        self._handles.clear()

    def new_snapshot(self, views: Sequence[Any], policy_epoch: int) -> list[dict[str, Any]]:
        self.invalidate_all()  # one live snapshot at a time
        expires = self._clock() + _TTL_S
        listed = []
        for view in views:
            handle = mint_opaque_ref("tgl_")
            self._handles[handle] = (view, policy_epoch, expires)
            listed.append(
                {
                    "handle": handle,
                    "display_name": view.display_name,
                    "chat_type": view.chat_type,
                    "username": view.username,
                }
            )
        return listed

    def take(self, handle: str, policy_epoch: int) -> Any:
        entry = self._handles.get(handle)
        if entry is None or entry[1] != policy_epoch or self._clock() >= entry[2]:
            self._handles.pop(handle, None)
            raise ValueError("unknown or expired selection")
        return entry[0]
