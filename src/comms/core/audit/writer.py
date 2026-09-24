"""AuditWriter: the only way anything appends to the comms chain (comms v0.3 A8, G1).

One process-wide lock (the COMMS append guard) covers ``BEGIN IMMEDIATE`` → appends →
``COMMIT`` → refresh of the external anchor to **exactly the head this transaction
committed**. No other audited transaction can commit between a commit and its anchor
refresh, so the anchor never moves backwards. If the refresh fails, the integrity latch
is set while the lock is still held, and ``AnchorFailed`` is raised.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core import refs, timeutil
from comms.core.audit.anchor import COMMS_ANCHOR, write_anchor
from comms.core.audit.chain import COMMS, append_event, append_guard, head
from comms.core.audit.integrity import latch_degraded
from comms.core.audit.specs import validate_audit_event
from comms.core.campaigns.events import journal_digest
from comms.core.canonical import jcs_dumps
from comms.core.keys.slots import KeySlotStore, load_active
from comms.core.storage.db import write_tx

__all__ = [
    "AnchorFailed",
    "AuditTx",
    "AuditWriter",
    "SlotChainKeys",
    "append_guard",
    "journal_digest",
]


class AnchorFailed(Exception):
    """The transaction committed but the anchor refresh failed; the latch is now set."""

    def __init__(self) -> None:
        super().__init__("AUDIT_INTEGRITY_DEGRADED")


class SlotChainKeys:
    """The comms chain MAC key per epoch, from the key slots (one epoch until Part B)."""

    def __init__(self, conn: Any, store: KeySlotStore) -> None:
        self._conn, self._store = conn, store

    def current(self) -> bytes:
        return load_active(self._conn, self._store, "audit-chain-key")[0]

    def current_id(self) -> str:
        return load_active(self._conn, self._store, "audit-chain-key")[1]

    def for_epoch(self, epoch: int) -> bytes:
        return self.current()


class AuditTx:
    """A handle on the open audited transaction."""

    def __init__(self, conn: Any, key: bytes, now: datetime) -> None:
        self.conn, self._key, self._now = conn, key, now
        self.appended = False

    def append(
        self,
        kind: str,
        *,
        subject_ref: str | None = None,
        subject_digest: str | None = None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        validate_audit_event(kind, subject_ref, subject_digest, payload)
        event = {
            "event_id": refs.mint("audit_event"),
            "ts": timeutil.iso(self._now),
            "kind": kind,
            "subject_ref": subject_ref,
            "subject_digest": subject_digest,
            "payload": jcs_dumps(dict(payload)).decode(),
        }
        result = append_event(self.conn, COMMS, self._key, event)
        self.appended = True
        return result


class AuditWriter:
    def __init__(
        self, conn: Any, keys: SlotChainKeys, anchor_path: Path, *, clock: Callable[[], datetime]
    ) -> None:
        self.conn, self._keys, self._anchor, self._clock = conn, keys, Path(anchor_path), clock

    def key_id(self) -> str:
        """The ID of the chain key new events are MACed under."""
        return self._keys.current_id()

    @contextmanager
    def transaction(self) -> Iterator[AuditTx]:
        with append_guard(COMMS):
            now = timeutil.utc(self._clock())
            key = self._keys.current()
            committed: dict[str, Any] | None = None
            with write_tx(self.conn):
                tx = AuditTx(self.conn, key, now)
                yield tx
                if tx.appended:
                    committed = head(
                        self.conn, COMMS
                    )  # this transaction's own head, read inside it
            if committed is not None:
                self._refresh(committed, now)

    def refresh_anchor(self) -> None:
        """Re-anchor to the committed head (a resumed cutover after a crash before its refresh)."""
        with append_guard(COMMS):
            committed = head(self.conn, COMMS)
            if committed is None:
                raise ValueError("cannot anchor an empty comms chain")
            self._refresh(committed, timeutil.utc(self._clock()))

    def _refresh(self, committed: Mapping[str, Any], now: datetime) -> None:
        """Called with the append guard held; a failure latches degraded before raising."""
        try:
            write_anchor(
                COMMS_ANCHOR,
                self._anchor,
                self._keys.for_epoch(committed["chain_epoch"]),
                chain_epoch=committed["chain_epoch"],
                chain_seq=committed["chain_seq"],
                event_id=committed["event_id"],
                event_mac=committed["event_mac"],
                now=timeutil.iso(now),
                guard_conn=self.conn,
            )
        except Exception:  # noqa: BLE001 -- any refresh failure degrades; the lock is still held
            latch_degraded(self.conn, reason="ANCHOR_REFRESH_FAILED", now=now)
            raise AnchorFailed from None
