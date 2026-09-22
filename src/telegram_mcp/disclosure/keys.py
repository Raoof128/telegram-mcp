"""Public verification-key registry (spec §23A.2, §33; design §4.2, §8).

Only public halves live here. A retained receipt must stay verifiable for
receipt retention plus the verification-key grace period, so retirement
never deletes a row — it stamps ``retired_at`` and leaves the public key
exportable.
"""

from __future__ import annotations

import sqlite3
from typing import Any

__all__ = [
    "PURPOSES",
    "current_verification_key",
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
