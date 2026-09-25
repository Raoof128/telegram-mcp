"""Historical verification, standing alone (comms v0.3 A1, A3).

The Telegram MCP surface is retired, but what it recorded stays verifiable: v1 and v2
disclosure receipts, and the legacy audit chain with its checkpoints. Everything here
needs only retained rows and historical public keys. It never imports the retired policy
engine, the coordinator, the budget ledger or the admin handlers;
``tests/security/test_v03_retired_surfaces.py`` pins that closure.
"""

from __future__ import annotations

import sqlite3

from comms.core.audit import chain as _core
from comms.core.audit.chain import ChainError
from comms.transports.telegram.disclosure.audit.profile import LEGACY_TELEGRAM
from comms.transports.telegram.disclosure.keys import checkpoint_public_for
from comms.transports.telegram.disclosure.receipts import verify_proof
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt

__all__ = [
    "missing_verification_keys",
    "verify_legacy_chain",
    "verify_proof",
    "verify_receipt_v1",
    "verify_receipt_v2",
]


def _stored_version(conn: sqlite3.Connection, disclosure_ref: str) -> int | None:
    row = conn.execute(
        "SELECT proof_version FROM disclosure_receipts WHERE disclosure_ref = ?", (disclosure_ref,)
    ).fetchone()
    return None if row is None else int(row[0])


def verify_receipt_v1(conn: sqlite3.Connection, disclosure_ref: str) -> bool:
    """A persisted ``tg-mcp-disclosure/v1`` receipt (historical, consent fields)."""
    return _stored_version(conn, disclosure_ref) == 1 and verify_persisted_receipt(
        conn, disclosure_ref
    )


def verify_receipt_v2(conn: sqlite3.Connection, disclosure_ref: str) -> bool:
    """A persisted ``tg-mcp-disclosure/v2`` receipt (owner-direct)."""
    return _stored_version(conn, disclosure_ref) == 2 and verify_persisted_receipt(
        conn, disclosure_ref
    )


def verify_legacy_chain(conn: sqlite3.Connection, chain_key: bytes) -> bool:
    """Every retained legacy link, and every checkpoint by its own registered key."""
    try:
        _core.verify_chain(conn, LEGACY_TELEGRAM, lambda _epoch: chain_key)
        _core.verify_checkpoints(conn, LEGACY_TELEGRAM, checkpoint_public_for(conn))
    except ChainError:
        return False
    return True


def missing_verification_keys(conn: sqlite3.Connection) -> set[str]:
    """Historical key coverage: every key a retained receipt or checkpoint names, if unregistered.

    A receipt needs a ``disclosure_proof`` key and a checkpoint an ``audit_checkpoint`` key;
    an empty result means every retained signature can still be checked.
    """
    needed = [
        (row[0], "disclosure_proof")
        for row in conn.execute("SELECT DISTINCT proof_key_id FROM disclosure_receipts")
    ] + [
        (row[0], "audit_checkpoint")
        for row in conn.execute("SELECT DISTINCT signing_key_id FROM audit_checkpoints")
    ]
    registered = {
        (row[0], row[1]) for row in conn.execute("SELECT key_id, purpose FROM verification_keys")
    }
    return {key_id for key_id, purpose in needed if (key_id, purpose) not in registered}
