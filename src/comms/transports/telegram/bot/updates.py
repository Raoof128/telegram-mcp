"""Bot API update polling with an atomic, persisted offset (comms v0.3 Task C12; A24).

``poll_once`` reads the mode and offset, makes one ``getUpdates`` long poll with no
transaction open, then ingests each update in its own transaction together with the offset
advance past it, so an update is retained exactly once and a failure leaves both unchanged.
An update already retained is a harmless duplicate. The adapter polls or takes a webhook, never
both: a configured webhook mode, or Telegram's own 409 for an active webhook, refuses polling.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from comms.core import timeutil
from comms.core.storage.db import write_tx
from comms.transports.telegram.bot.http import BotApi, BotResponse, BotTransportError

__all__ = [
    "ALLOWED_UPDATES",
    "BotPoller",
    "PollReport",
    "PollingRefused",
    "set_update_mode",
    "state",
]

ALLOWED_UPDATES = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)
LONG_POLL_SECONDS = 25
Mode = Literal["polling", "webhook"]


class PollingRefused(Exception):
    """Polling is not this installation's update mode (A24). Fixed message."""

    def __init__(self) -> None:
        super().__init__("bot updates are delivered by webhook; polling is refused")


@dataclass(frozen=True)
class PollReport:
    outcome: Literal["ok", "unavailable", "malformed"]
    ingested: int = 0
    duplicates: int = 0


def state(conn: Any) -> tuple[Mode, int]:
    row = conn.execute("SELECT mode, next_offset FROM bot_update_offset WHERE id = 1").fetchone()
    return (row[0], int(row[1])) if row else ("polling", 0)


def set_update_mode(conn: Any, mode: str, *, now: datetime) -> None:
    if mode not in ("polling", "webhook"):
        raise ValueError("update mode is polling or webhook")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO bot_update_offset (id, mode, next_offset, updated_at) VALUES (1, ?, 0, ?)"
            " ON CONFLICT (id) DO UPDATE SET mode = excluded.mode, updated_at = excluded.updated_at",
            (mode, timeutil.iso(now)),
        )


def _kind(update: Mapping[str, Any]) -> tuple[str, int | None]:
    kind = next((k for k in ALLOWED_UPDATES if isinstance(update.get(k), dict)), "unrecognised")
    chat = update[kind].get("chat") if kind != "unrecognised" else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    return kind, chat_id if type(chat_id) is int else None


class BotPoller:
    def __init__(
        self,
        api: BotApi,
        conn: Any,
        *,
        clock: Callable[[], datetime],
        on_update: Callable[[Any, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._api, self._conn, self._clock, self._on_update = api, conn, clock, on_update

    def poll_once(self) -> PollReport:
        mode, offset = state(self._conn)
        if mode != "polling":
            raise PollingRefused
        params = {
            "offset": offset,
            "timeout": LONG_POLL_SECONDS,
            "allowed_updates": list(ALLOWED_UPDATES),
        }
        try:
            response = self._api.call("getUpdates", params)
        except BotTransportError:
            return PollReport("unavailable")
        updates = self._updates(response)
        if updates is None:
            return PollReport("unavailable")
        ingested = duplicates = 0
        for update in updates:
            update_id = update.get("update_id") if isinstance(update, dict) else None
            if type(update_id) is not int or update_id < 0:
                return PollReport("malformed", ingested, duplicates)
            if self._ingest(update, update_id):
                ingested += 1
            else:
                duplicates += 1
        return PollReport("ok", ingested, duplicates)

    def _updates(self, response: BotResponse) -> list[Any] | None:
        envelope = response.envelope
        if envelope is not None and envelope.get("error_code") == 409:
            raise PollingRefused  # Telegram: a webhook is active
        if response.http_status != 200 or envelope is None or envelope.get("ok") is not True:
            return None
        result = envelope.get("result")
        return result if isinstance(result, list) else None

    def _ingest(self, update: Mapping[str, Any], update_id: int) -> bool:
        kind, chat_id = _kind(update)
        now = timeutil.iso(self._clock())
        with write_tx(self._conn):
            inserted = self._conn.execute(
                "INSERT INTO bot_updates (update_id, chat_id, kind, payload, received_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (update_id) DO NOTHING",
                (update_id, chat_id, kind, json.dumps(update, sort_keys=True), now),
            ).rowcount
            if inserted and self._on_update is not None:
                self._on_update(self._conn, update)
            self._conn.execute(
                "INSERT INTO bot_update_offset (id, mode, next_offset, updated_at)"
                " VALUES (1, 'polling', ?, ?) ON CONFLICT (id) DO UPDATE SET"
                " next_offset = max(next_offset, excluded.next_offset), updated_at = excluded.updated_at",
                (update_id + 1, now),
            )
        return bool(inserted)
