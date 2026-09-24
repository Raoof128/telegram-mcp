"""The Telegram side of retention (comms v0.3 Task B17): the four legacy phases.

Legacy audit rows can be deleted only while ``maintenance_flags.truncating = 1``, which
only ``truncate_chain_before`` sets, inside the same transaction as its delete; a trigger
refuses every other delete (legacy ``Migration(3)``). Timestamps here use the legacy
second-precision spelling.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from comms.core.storage.db import write_tx
from comms.transports.telegram.runtime.cutover_barrier import legacy_verifier

__all__ = ["TelegramLegacyRetention"]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class TelegramLegacyRetention:
    def __init__(
        self, conn: Any, chain_key: bytes, public_for: Callable[[str], bytes | None]
    ) -> None:
        self.conn = conn
        self.verify = legacy_verifier(chain_key, public_for)

    def purge_exposure(self, cutoff: datetime) -> int:
        with write_tx(self.conn):
            return int(
                self.conn.execute(
                    "DELETE FROM exposure_ledger WHERE ts < ?", (_stamp(cutoff),)
                ).rowcount
            )

    def truncate_chain_before(self, root: Mapping[str, Any]) -> int:
        with write_tx(self.conn):
            self.conn.execute("UPDATE maintenance_flags SET value = 1 WHERE name = 'truncating'")
            deleted = self.conn.execute(
                "DELETE FROM audit_events WHERE chain_epoch < ? OR (chain_epoch = ? AND chain_seq < ?)",
                (root["chain_epoch"], root["chain_epoch"], root["chain_seq"]),
            ).rowcount
            self.conn.execute("UPDATE maintenance_flags SET value = 0 WHERE name = 'truncating'")
        return int(deleted)

    def purge_receipts(self, cutoff: datetime) -> int:
        with write_tx(self.conn):
            return int(
                self.conn.execute(
                    "DELETE FROM disclosure_receipts WHERE committed_at < ?"
                    " AND NOT EXISTS (SELECT 1 FROM exposure_ledger e"
                    "   WHERE e.disclosure_ref = disclosure_receipts.disclosure_ref)"
                    " AND NOT EXISTS (SELECT 1 FROM audit_events a"
                    "   WHERE a.disclosure_ref = disclosure_receipts.disclosure_ref)",
                    (_stamp(cutoff),),
                ).rowcount
            )

    def purge_message_refs(self, cutoff: datetime, *, now: datetime) -> int:
        with write_tx(self.conn):
            return int(
                self.conn.execute(
                    "DELETE FROM message_refs WHERE last_used_at < ?"
                    " AND NOT EXISTS (SELECT 1 FROM cursors c WHERE c.expires_at > ?"
                    "   AND instr(c.state_json, message_refs.message_ref) > 0)",
                    (_stamp(cutoff), _stamp(now)),
                ).rowcount
            )
