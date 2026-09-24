"""The MTProto update stream consumer (comms v0.3 Task C21; A24, A42).

Consumes the neutral updates the adapter translates from the single session's stream (the
session admits one consumer). ``message_id`` updates bind a send's provider ref through the
``random_id`` persisted before the call (``correlate_message_id``); a repeat or an unknown key
changes nothing. ``message`` updates are retained once each in ``user_updates`` and handed to
``sink`` as an ``InboundEvent`` in the same transaction, so a failing sink leaves no trace and a
duplicate is harmless.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.providers.protocols import InboundEvent
from comms.core.storage.db import write_tx
from comms.transports.telegram.telegram.updates_view import NeutralUpdate
from comms.transports.telegram.user.send import correlate_message_id

__all__ = ["ConsumeReport", "UserUpdateConsumer"]


@dataclass(frozen=True)
class ConsumeReport:
    bound: int = 0
    unmatched: int = 0
    ingested: int = 0
    duplicates: int = 0


class UserUpdateConsumer:
    def __init__(
        self,
        conn: Any,
        *,
        clock: Callable[[], datetime],
        sink: Callable[[Any, InboundEvent], None] | None = None,
    ) -> None:
        self._conn, self._clock, self._sink = conn, clock, sink

    def consume(self, updates: Iterable[NeutralUpdate]) -> ConsumeReport:
        bound = unmatched = ingested = duplicates = 0
        for update in updates:
            now = self._clock()
            with write_tx(self._conn):
                if update.kind == "message_id" and update.random_id is not None:
                    if correlate_message_id(
                        self._conn, update.random_id, update.message_id, now=now
                    ):
                        bound += 1
                    else:
                        unmatched += 1
                elif update.kind == "message" and update.chat is not None:
                    if self._ingest(update, now):
                        ingested += 1
                    else:
                        duplicates += 1
        return ConsumeReport(bound, unmatched, ingested, duplicates)

    def _ingest(self, update: NeutralUpdate, now: datetime) -> bool:
        ref = f"{update.chat}:{update.message_id}"
        inserted = self._conn.execute(
            "INSERT INTO user_updates (event_ref, chat_id, message_id, kind, payload, received_at)"
            " VALUES (?, ?, ?, 'message', ?, ?) ON CONFLICT (event_ref) DO NOTHING",
            (
                ref,
                update.chat,
                update.message_id,
                json.dumps(dict(update.payload), sort_keys=True),
                timeutil.iso(now),
            ),
        ).rowcount
        if inserted and self._sink is not None:
            self._sink(self._conn, InboundEvent("telegram", "message", ref, dict(update.payload)))
        return bool(inserted)
