"""The Telegram side of the v0.3 cutover (R-003): the ingress gate and the legacy port.

``CutoverGate`` wraps the legacy ingress ASGI app: once closed it refuses new requests
with 503 and counts in-flight ones down. ``TelegramLegacyPort`` verifies, seals and
anchors the legacy chain. The seal is enforced by the legacy database itself (a trigger
refuses every audit insert once ``audit.append_state`` is ``sealed``), not only here.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core import timeutil
from comms.core.audit.cutover import LegacySeal
from comms.core.canonical import jcs_dumps
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit import anchor, chain
from comms.transports.telegram.storage.settings import get_setting, validate_setting

__all__ = ["CutoverGate", "TelegramLegacyPort"]


class CutoverGate:
    """An ASGI wrapper with a closed flag and an in-flight counter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight = 0
        self.closed = False

    def enter(self) -> bool:
        with self._lock:
            if self.closed:
                return False
            self._inflight += 1
            return True

    def exit(self) -> None:
        with self._lock:
            self._inflight -= 1

    def close(self) -> None:
        with self._lock:
            self.closed = True

    def drained(self) -> bool:
        with self._lock:
            return self._inflight == 0

    def wrap(self, app: Any) -> Any:
        async def gated(scope: Any, receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return
            if not self.enter():
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"retired in comms v0.3"})
                return
            try:
                await app(scope, receive, send)
            finally:
                self.exit()

        return gated


def _marker(now: datetime) -> dict[str, Any]:
    return {
        "event_id": chain.mint_event_id(),
        "ts": timeutil.iso(now),
        "tool_name": "system.cutover_final",
        "principal_ref": None,
        "client_ref": None,
        "account_ref": None,
        "peer_ref": None,
        "project_ref": None,
        "project_count": None,
        "policy_epoch": None,
        "result_count": None,
        "duration_ms": None,
        "telegram_rpc_count": None,
        "status": "ok",
        "error_code": None,
        "disclosure_ref": None,
    }


class TelegramLegacyPort:
    def __init__(
        self,
        conn: Any,
        chain_key: bytes,
        checkpoint_key: bytes,
        anchor_path: Path,
        gate: CutoverGate,
    ) -> None:
        self.conn, self._key, self._cp_key = conn, chain_key, checkpoint_key
        self._anchor, self.gate = Path(anchor_path), gate

    def close_ingress(self) -> None:
        self.gate.close()

    def drained(self) -> bool:
        return self.gate.drained()

    def verify(self) -> bool:
        return anchor.derive_integrity(self.conn, self._key, self._anchor) == anchor.CLEAN

    def sealed_record(self) -> LegacySeal | None:
        if get_setting(self.conn, "audit.append_state") != "sealed":
            return None
        row = self.conn.execute(
            "SELECT checkpoint_ref, chain_epoch, chain_seq, last_event_id, last_event_mac, created_at,"
            " signing_key_id, signature FROM audit_checkpoints ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
        ).fetchone()
        names = (
            "checkpoint_ref",
            "chain_epoch",
            "chain_seq",
            "last_event_id",
            "last_event_mac",
            "created_at",
            "signing_key_id",
            "signature",
        )
        record = dict(zip(names, row, strict=True))
        return LegacySeal(
            final_epoch=int(record["chain_epoch"]),
            final_head=str(record["last_event_mac"]),
            checkpoint_digest=hashlib.sha256(jcs_dumps(record)).hexdigest(),
            checkpoint_key_id=str(record["signing_key_id"]),
        )

    def seal(self, *, now: datetime) -> LegacySeal:
        value = json.dumps(validate_setting("audit.append_state", "sealed"), separators=(",", ":"))
        with write_tx(self.conn):
            chain.append_event(self.conn, self._key, _marker(now))
            chain.insert_checkpoint(self.conn, self._cp_key, now=timeutil.iso(now))
            self.conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES ('audit.append_state', ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                (value, timeutil.iso(now)),
            )
        seal = self.sealed_record()
        assert seal is not None
        return seal

    def refresh_anchor(self, *, now: datetime) -> None:
        current = chain.head(self.conn)
        if current is None:
            raise ValueError("cannot anchor an empty legacy chain")
        anchor.write_anchor(self._anchor, self._key, now=timeutil.iso(now), **current)

    def revoke_client_auth(self, *, now: datetime) -> tuple[int, int]:
        raise NotImplementedError("Task A11")
