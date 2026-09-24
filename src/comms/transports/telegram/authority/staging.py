"""Staged policy changes, ``tps_`` handles (Phase-5 design §2.3).

In daemon memory only, for ten minutes, at most ``max_live`` at once. A
stage is bound to the admin peer, the owner principal, the active account,
the security epoch, and the base digest the change was computed against. A
mismatch on any of them refuses rather than re-diffing silently: the
operator approved a diff, not a moving target.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from comms.transports.telegram.opaque import mint_opaque_ref

__all__ = ["Binding", "Staged", "StagingRegistry"]

TTL_S = 600.0
MAX_LIVE = 64


@dataclass(frozen=True)
class Binding:
    peer_uid: int | None
    principal_ref: str
    account_ref: str
    security_epoch: int


@dataclass(frozen=True)
class Staged:
    handle: str
    binding: Binding
    base_digest: str
    payload: Any
    diff: Mapping[str, Any]
    expires_at: float


class StagingRegistry:
    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_live: int = MAX_LIVE
    ) -> None:
        self._clock = clock
        self._max = max_live
        self._items: dict[str, Staged] = {}

    def __len__(self) -> int:
        return len(self._items)

    def _sweep(self) -> None:
        now = self._clock()
        for handle in [h for h, s in self._items.items() if s.expires_at <= now]:
            del self._items[handle]

    def stage(
        self, *, binding: Binding, base_digest: str, payload: Any, diff: Mapping[str, Any]
    ) -> str:
        self._sweep()
        if len(self._items) >= self._max:
            raise ValueError("too many staged changes; let some expire")
        handle = mint_opaque_ref("tps_")
        self._items[handle] = Staged(
            handle, binding, base_digest, payload, diff, self._clock() + TTL_S
        )
        return handle

    def get(self, handle: str, *, binding: Binding, current_base: str) -> Staged:
        self._sweep()
        staged = self._items.get(handle)
        if staged is None or staged.binding != binding or staged.base_digest != current_base:
            raise ValueError("unknown, expired or stale staged change")
        return staged
