"""The resumable webhook fan-out (comms v0.3 Task C29; G12, A43).

For each inbox row not yet complete, in order, each effect once:

1. the WhatsVault archive (external, idempotent by message id), then ``archive_done``;
2. the mirrored window (``mirror_window_in_tx``, monotone), committed with ``window_done``;
3. provider statuses (``apply_provider_update``, idempotent by event ref; an early status is
   ``pending_match`` until its send is bound), committed with ``status_done``;
4. ``completed_at``.

A crash between the archive and its flag replays an idempotent import; every comms.db effect
commits with its own flag, so it happens once. A verified body that is not Meta's documented
shape completes with nothing applied and is counted. ``crash_at`` is a test seam only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.delivery.operations import apply_provider_update
from comms.core.delivery.window import mirror_window_in_tx
from comms.core.storage.db import io_guard, write_tx
from comms.transports.whatsapp.numbers import e164
from comms.transports.whatsapp.webhooks.normalize import parse

__all__ = ["CRASH_POINTS", "Archive", "WebhookWorker", "WorkerCrash", "WorkerReport"]

CRASH_POINTS = ("after_archive_before_flag", "after_window_before_flag", "after_status_before_flag")


_SET_FLAG = {  # fixed statements: a flag name never becomes SQL text
    "archive_done": "UPDATE webhook_inbox SET archive_done = 1 WHERE id = ?",
    "window_done": "UPDATE webhook_inbox SET window_done = 1 WHERE id = ?",
    "status_done": "UPDATE webhook_inbox SET status_done = 1 WHERE id = ?",
}


class WorkerCrash(BaseException):
    """Raised only by the ``crash_at`` seam; BaseException so no handler swallows it."""


class Archive(Protocol):
    def ingest(self, raw: bytes) -> None: ...


@dataclass(frozen=True)
class WorkerReport:
    completed: int = 0
    malformed: int = 0


class WebhookWorker:
    def __init__(
        self,
        conn: Any,
        archive: Archive,
        *,
        clock: Callable[[], datetime],
        crash_at: str | None = None,
    ) -> None:
        if crash_at is not None and crash_at not in CRASH_POINTS:
            raise ValueError("unknown crash point")
        self._conn, self._archive, self._clock, self._crash_at = conn, archive, clock, crash_at

    def __repr__(self) -> str:
        return "WebhookWorker(<redacted>)"

    def _crash(self, point: str) -> None:
        if self._crash_at == point:
            raise WorkerCrash(point)

    def run_once(self) -> WorkerReport:
        rows = self._conn.execute(
            "SELECT id, body, archive_done, window_done, status_done FROM webhook_inbox"
            " WHERE completed_at IS NULL ORDER BY id"
        ).fetchall()
        completed = malformed = 0
        for row_id, raw, archive_done, window_done, status_done in rows:
            raw = bytes(raw)
            try:
                inbound, statuses = parse(raw)
            except ValueError:
                inbound, statuses, archive_done = [], [], 1  # nothing for the archive either
                malformed += 1
            if not archive_done:
                io_guard(self._conn)
                self._archive.ingest(raw)
                self._crash("after_archive_before_flag")
            self._flag(row_id, "archive_done")
            if not window_done:
                with write_tx(self._conn):
                    for message in inbound:
                        identity_id = self._identity(message.from_number)
                        if identity_id is not None:
                            mirror_window_in_tx(
                                self._conn,
                                identity_id,
                                message.at,
                                message.wamid,
                                now=self._clock(),
                            )
                    self._crash("after_window_before_flag")
                    self._set(row_id, "window_done")
            if not status_done:
                with write_tx(self._conn):
                    for report in statuses:
                        apply_provider_update(
                            self._conn,
                            "whatsapp",
                            report.event_ref,
                            report.wamid,
                            report.status,
                            now=self._clock(),
                        )
                    self._crash("after_status_before_flag")
                    self._set(row_id, "status_done")
            with write_tx(self._conn):
                self._conn.execute(
                    "UPDATE webhook_inbox SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
                    (self._stamp(), row_id),
                )
            completed += 1
        return WorkerReport(completed, malformed)

    def _stamp(self) -> str:
        return timeutil.iso(self._clock())

    def _flag(self, row_id: int, column: str) -> None:
        with write_tx(self._conn):
            self._set(row_id, column)

    def _set(self, row_id: int, column: str) -> None:
        self._conn.execute(_SET_FLAG[column], (row_id,))

    def _identity(self, number: str) -> int | None:
        try:
            identity = e164(number)
        except ValueError:
            return None
        row = self._conn.execute(
            "SELECT id FROM delivery_identities WHERE transport = 'whatsapp' AND identity = ?",
            (identity,),
        ).fetchone()
        return int(row[0]) if row else None
