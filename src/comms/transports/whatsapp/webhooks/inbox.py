"""The durable webhook inbox (comms v0.3 Task C29; G12, A43).

The ingress hands every verified raw body to ``accept``: one ``INSERT OR IGNORE`` keyed by the
body's SHA-256, committed before the ingress answers 200. A duplicate is ignored and still
acknowledged; it wakes the worker and never means "already processed". The worker finishes
whatever a row still lacks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.storage.db import write_tx

__all__ = ["Inbox"]


class Inbox:
    def __init__(
        self, conn: Any, *, clock: Callable[[], datetime], wake: Callable[[], None] | None = None
    ) -> None:
        self._conn, self._clock, self._wake = conn, clock, wake

    def __repr__(self) -> str:
        return "Inbox(<redacted>)"

    def store(self, raw: bytes) -> bool:
        """Record a verified body; True when it is new. Commits before returning."""
        ref = hashlib.sha256(raw).hexdigest()
        with write_tx(self._conn):
            inserted = self._conn.execute(
                "INSERT INTO webhook_inbox (provider_event_ref, received_at, body) VALUES (?, ?, ?)"
                " ON CONFLICT (provider_event_ref) DO NOTHING",
                (ref, timeutil.iso(self._clock()), raw),
            ).rowcount
        if self._wake is not None:
            self._wake()
        return bool(inserted)

    async def accept(self, raw: bytes) -> None:
        """The ingress's ``accept``: store, then the ingress may acknowledge."""
        self.store(raw)
