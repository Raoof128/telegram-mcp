"""Public verification-key registry (spec §23A.2, §33; design §4.2, §8).

Only public halves live here. A retained receipt must stay verifiable for
receipt retention plus the verification-key grace period, so retirement
never deletes a row — it stamps ``retired_at`` and leaves the public key
exportable.
"""

from __future__ import annotations

import base64
import sqlite3
from collections.abc import Callable
from typing import Any

__all__ = [
    "PURPOSES",
    "checkpoint_public_for",
    "current_verification_key",
    "ensure_current_published",
    "export_verification_keys",
    "lookup_verification_key",
    "publish_verification_key",
    "retire_verification_key",
]

# Mirrors the CHECK constraint on verification_keys.purpose (spec §12.2).
PURPOSES: tuple[str, ...] = ("disclosure_proof", "audit_checkpoint", "policy_backup")

_COLUMNS = "key_id, purpose, algorithm, public_key_b64url, activated_at, retired_at"


def _row(cursor_row: sqlite3.Row | tuple[Any, ...] | None) -> dict[str, Any] | None:
    if cursor_row is None:
        return None
    return dict(zip(_COLUMNS.split(", "), cursor_row, strict=True))


def publish_verification_key(
    conn: sqlite3.Connection,
    *,
    key_id: str,
    purpose: str,
    algorithm: str,
    public_key_b64url: str,
    activated_at: str,
) -> None:
    """Record one public half. Private material must never reach this call."""
    if purpose not in PURPOSES:
        raise ValueError("unknown verification-key purpose")
    conn.execute(
        "INSERT INTO verification_keys"
        " (key_id, purpose, algorithm, public_key_b64url, activated_at, retired_at)"
        " VALUES (?, ?, ?, ?, ?, NULL)",
        (key_id, purpose, algorithm, public_key_b64url, activated_at),
    )
    conn.commit()


def retire_verification_key(conn: sqlite3.Connection, *, key_id: str, retired_at: str) -> None:
    """Stamp retirement. The row and its public key stay for verification."""
    conn.execute(
        "UPDATE verification_keys SET retired_at = ? WHERE key_id = ?", (retired_at, key_id)
    )
    conn.commit()


def current_verification_key(conn: sqlite3.Connection, purpose: str) -> dict[str, Any] | None:
    """The one unretired key for a purpose, newest activation first."""
    if purpose not in PURPOSES:
        raise ValueError("unknown verification-key purpose")
    return _row(
        conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys"
            " WHERE purpose = ? AND retired_at IS NULL"
            " ORDER BY activated_at DESC LIMIT 1",
            (purpose,),
        ).fetchone()
    )


def lookup_verification_key(conn: sqlite3.Connection, key_id: str) -> dict[str, Any] | None:
    """Resolve any key id, current or historical (Appendix K.2 step 4)."""
    return _row(
        conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys WHERE key_id = ?", (key_id,)
        ).fetchone()
    )


def checkpoint_public_for(conn: sqlite3.Connection) -> Callable[[str], bytes | None]:
    """A registry-backed ``public_for``: an ``audit_checkpoint`` key's raw public bytes.

    The caller recomputes the key id from the bytes; a stored label is never trusted
    by itself (spec §9.6.1).
    """

    def public_for(key_id: str) -> bytes | None:
        key = lookup_verification_key(conn, key_id)
        if key is None or key["purpose"] != "audit_checkpoint":
            return None
        encoded = key["public_key_b64url"]
        try:
            return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except ValueError:
            return None

    return public_for


def export_verification_keys(
    conn: sqlite3.Connection, *, key_id: str | None = None
) -> list[dict[str, Any]]:
    """Backing query for ``telegram-mcp disclosure key [--id <key-id>]``."""
    if key_id is None:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys ORDER BY activated_at"
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys WHERE key_id = ?", (key_id,)
        ).fetchall()
    return [row for row in (_row(r) for r in rows) if row is not None]


def ensure_current_published(
    conn: sqlite3.Connection, *, purpose: str, private_seed: bytes, now: str
) -> str:
    """Publish an Ed25519 key's public half unless it is already current. Returns the key_id."""
    import base64
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.from_private_bytes(private_seed).public_key().public_bytes_raw()
    key_id = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    current = current_verification_key(conn, purpose)
    if current is None or current["key_id"] != key_id:
        publish_verification_key(
            conn,
            key_id=key_id,
            purpose=purpose,
            algorithm="Ed25519",
            public_key_b64url=base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
            activated_at=now,
        )
    return key_id
