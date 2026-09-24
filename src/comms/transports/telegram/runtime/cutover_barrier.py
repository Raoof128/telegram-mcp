"""The Telegram side of the v0.3 cutover (R-003): the ingress gate and the legacy port.

``CutoverGate`` wraps the legacy ingress ASGI app: once closed it refuses new requests
with 503 and counts in-flight ones down. ``TelegramLegacyPort`` verifies, seals and
anchors the legacy chain, and retires `tgml1`. The seal and the retirement are enforced
by the legacy database itself (triggers refuse every audit insert once
``audit.append_state`` is ``sealed``, and every bearer client once ``auth.tgml1_state`` is
``revoked``), not only here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core import timeutil
from comms.core.audit.cutover import SEAL_CHECKPOINT_FIELDS, LegacySeal, seal_of
from comms.core.audit.verify_all import LegacyVerify
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit import anchor, chain
from comms.transports.telegram.disclosure.audit.profile import LEGACY_TELEGRAM
from comms.transports.telegram.ipc.leases import revoke_client_auth
from comms.transports.telegram.storage.settings import get_setting, put_setting

__all__ = ["CutoverGate", "TelegramLegacyPort", "legacy_verifier"]

_FINAL_MARKER = "system.cutover_final"


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


def _final_checkpoint_seal(conn: Any) -> LegacySeal | None:
    row = conn.execute(
        f"SELECT {', '.join(SEAL_CHECKPOINT_FIELDS)} FROM audit_checkpoints"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
    ).fetchone()
    return None if row is None else seal_of(dict(zip(SEAL_CHECKPOINT_FIELDS, row, strict=True)))


def legacy_verifier(chain_key: bytes, public_for: Callable[[str], bytes | None]) -> LegacyVerify:
    """What ``verify_all`` needs to walk this legacy chain, with only public checkpoint keys."""
    return LegacyVerify(
        profile=LEGACY_TELEGRAM,
        chain_domain=LEGACY_TELEGRAM.event_domain.decode(),
        key_for_epoch=lambda _epoch: chain_key,
        public_for=public_for,
        is_final_marker=lambda event: event["tool_name"] == _FINAL_MARKER,
        is_sealed=lambda conn: get_setting(conn, "audit.append_state") == "sealed",
    )


def _marker(now: datetime) -> dict[str, Any]:
    return {
        "event_id": chain.mint_event_id(),
        "ts": timeutil.iso(now),
        "tool_name": _FINAL_MARKER,
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
        key_dir: Path,
    ) -> None:
        self.conn, self._key, self._cp_key = conn, chain_key, checkpoint_key
        self._anchor, self.gate, self._key_dir = Path(anchor_path), gate, Path(key_dir)

    chain_domain = LEGACY_TELEGRAM.event_domain.decode()

    def close_ingress(self) -> None:
        self.gate.close()

    def drained(self) -> bool:
        return self.gate.drained()

    def verify(self) -> bool:
        return anchor.derive_integrity(self.conn, self._key, self._anchor) == anchor.CLEAN

    def sealed_record(self) -> LegacySeal | None:
        if get_setting(self.conn, "audit.append_state") != "sealed":
            return None
        return _final_checkpoint_seal(self.conn)

    def seal(self, *, now: datetime) -> LegacySeal:
        with write_tx(self.conn):
            chain.append_event(self.conn, self._key, _marker(now))
            chain.insert_checkpoint(self.conn, self._cp_key, now=timeutil.iso(now))
            put_setting(self.conn, "audit.append_state", "sealed", now=timeutil.iso(now))
        seal = self.sealed_record()
        assert seal is not None
        return seal

    def refresh_anchor(self, *, now: datetime) -> None:
        current = chain.head(self.conn)
        if current is None:
            raise ValueError("cannot anchor an empty legacy chain")
        anchor.write_anchor(self._anchor, self._key, now=timeutil.iso(now), **current)

    def revoke_client_auth(self, *, now: datetime) -> tuple[int, int]:
        return revoke_client_auth(self.conn, self._key_dir, now=timeutil.iso(now))
