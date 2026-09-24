"""The legacy Telegram audit chain: a thin binding of the core engine (comms v0.3 Task A5).

Every MAC, genesis value and checkpoint signature is computed by ``comms.core.audit.chain``
under the ``LEGACY_TELEGRAM`` profile, byte-identical to the pre-v0.3 chain
(tests/fixtures/audit/legacy_chain_vectors.json). This module keeps the legacy call
signatures, the Crockford ``evt_`` minter and the §26.5 checkpoint cadence. The caller
owns every transaction; ``comms.core.storage.db.write_tx`` is the one transaction helper.
"""

from __future__ import annotations

import base64
import secrets
import time
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from comms.core.audit import chain as _core
from comms.core.audit.chain import ChainError
from comms.core.keys import ids
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit.profile import (
    ADMIN_EVENTS,
    CHECKPOINT_DOMAIN,
    EVENT_COLUMNS,
    EVENT_DOMAIN,
    GENESIS_DOMAIN,
    LEGACY_TELEGRAM,
)

__all__ = [
    "ADMIN_EVENTS",
    "APPEND_GUARD",
    "CHECKPOINT_DOMAIN",
    "EVENT_COLUMNS",
    "EVENT_DOMAIN",
    "GENESIS_DOMAIN",
    "LEGACY_TELEGRAM",
    "ChainError",
    "append_event",
    "checkpoint_due",
    "event_mac",
    "genesis_mac",
    "head",
    "insert_checkpoint",
    "mint_event_id",
    "require_immediate_transaction",
    "verify_chain",
    "verify_checkpoints",
    "verify_checkpoints_registry",
    "write_checkpoint",
]

# One process-wide guard for every legacy audit append (Phase-5 design §2.1, 0B G5).
APPEND_GUARD = _core.append_guard(LEGACY_TELEGRAM)

# Appendix C illustrates evt_01J...: Crockford base32, uppercase, 26 chars.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def require_immediate_transaction(conn: Any) -> None:
    """Refuse to append outside a caller-owned write transaction."""
    if not conn.in_transaction:
        raise ChainError("an open BEGIN IMMEDIATE transaction is required")


def _base32_crockford(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_CROCKFORD[remainder])
    return "".join(reversed(chars))


def mint_event_id() -> str:
    """``evt_`` plus a 26-character Crockford base32 ULID."""
    timestamp = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return "evt_" + _base32_crockford(timestamp, 10) + _base32_crockford(randomness, 16)


def genesis_mac(chain_epoch: int) -> str:
    return _core.genesis_mac(LEGACY_TELEGRAM, chain_epoch)


def event_mac(
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    prev_event_mac: str,
    event: Mapping[str, Any],
) -> str:
    return _core.event_mac(
        LEGACY_TELEGRAM,
        chain_key,
        chain_epoch=chain_epoch,
        chain_seq=chain_seq,
        prev_event_mac=prev_event_mac,
        event=event,
    )


def head(conn: Any) -> dict[str, Any] | None:
    return _core.head(conn, LEGACY_TELEGRAM)


def append_event(conn: Any, chain_key: bytes, event: Mapping[str, Any]) -> dict[str, Any]:
    """Append one event. **The caller owns the transaction** (§23A.3: one atomic commit)."""
    require_immediate_transaction(conn)
    return _core.append_event(conn, LEGACY_TELEGRAM, chain_key, event)


def verify_chain(conn: Any, chain_key: bytes) -> None:
    """Recompute every retained link. Raises ``ChainError`` on any mismatch."""
    _core.verify_chain(conn, LEGACY_TELEGRAM, lambda _epoch: chain_key)


def _checkpoint_message(row: Mapping[str, Any]) -> bytes:
    return _core.checkpoint_message(LEGACY_TELEGRAM, row)


def insert_checkpoint(conn: Any, checkpoint_key: bytes, *, now: str) -> dict[str, Any]:
    """Sign the current head inside the caller's transaction. Never commits."""
    require_immediate_transaction(conn)
    return _core.insert_checkpoint(conn, LEGACY_TELEGRAM, checkpoint_key, now=now)


def write_checkpoint(conn: Any, checkpoint_key: bytes, *, now: str) -> dict[str, Any]:
    """Compatibility wrapper: one checkpoint in its own transaction."""
    with write_tx(conn):
        return insert_checkpoint(conn, checkpoint_key, now=now)


def verify_checkpoints(conn: Any, checkpoint_public: bytes) -> None:
    """Verify every retained checkpoint signature against one public key."""
    _core.verify_checkpoints(conn, LEGACY_TELEGRAM, lambda _key_id: checkpoint_public)


def verify_checkpoints_registry(conn: Any) -> str:
    """``"none"`` | ``"verified"`` | ``"failed"``: each checkpoint by its own recorded key."""
    from comms.transports.telegram.disclosure.keys import lookup_verification_key

    names = (*LEGACY_TELEGRAM.signed_checkpoint_fields, "signature", "signing_key_id")
    rows = conn.execute(
        f"SELECT {', '.join(names)} FROM audit_checkpoints ORDER BY chain_epoch, chain_seq"
    ).fetchall()
    if not rows:
        return "none"
    for row in rows:
        record = dict(zip(names, row, strict=True))
        key = lookup_verification_key(conn, record["signing_key_id"])
        if key is None or key["purpose"] != "audit_checkpoint":
            return "failed"
        raw = base64.urlsafe_b64decode(
            key["public_key_b64url"] + "=" * (-len(key["public_key_b64url"]) % 4)
        )
        if ids.ed25519_key_id(raw) != record["signing_key_id"]:
            return "failed"  # a stored label is never trusted by itself (spec §9.6.1)
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(
                bytes.fromhex(record["signature"]), _checkpoint_message(record)
            )
        except (InvalidSignature, ValueError):
            return "failed"
    return "verified"


def checkpoint_due(conn: Any, *, events_since: int, seconds_since: int) -> bool:
    """§26.5 cadence: at least every 500 events or 60 minutes, whichever first."""
    from comms.transports.telegram.storage.settings import get_setting

    return events_since >= get_setting(conn, "audit.checkpoint_cadence_events") or (
        seconds_since >= get_setting(conn, "audit.checkpoint_cadence_seconds")
    )
