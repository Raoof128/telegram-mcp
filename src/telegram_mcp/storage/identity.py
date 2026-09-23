"""Identity bootstrap (design §3.8; spec §10.4, §10.5, §12.2)."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import UTC, datetime

from telegram_mcp.disclosure.audit.chain import immediate_transaction
from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["OWNER_PRINCIPAL", "ensure_account", "ensure_owner_principal"]

OWNER_PRINCIPAL = "local_single_principal"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_owner_principal(conn: sqlite3.Connection, *, privacy_key: bytes) -> int:
    key = hmac.new(privacy_key, OWNER_PRINCIPAL.encode(), hashlib.sha256).hexdigest()
    row = conn.execute("SELECT id FROM principals WHERE principal_key = ?", (key,)).fetchone()
    if row is not None:
        return int(row[0])
    with immediate_transaction(conn):
        cursor = conn.execute(
            "INSERT INTO principals (principal_ref, principal_key, auth_mode, label, created_at)"
            " VALUES (?, ?, 'local', 'owner', ?)",
            (mint_opaque_ref("prn_"), key, _now()),
        )
    assert cursor.lastrowid is not None  # an INSERT always sets it
    return int(cursor.lastrowid)


def ensure_account(conn: sqlite3.Connection, *, telegram_user_id: int, label: str | None) -> int:
    row = conn.execute(
        "SELECT id FROM accounts WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()
    if row is not None:
        return int(row[0])
    principal = conn.execute("SELECT id FROM principals ORDER BY id LIMIT 1").fetchone()
    if principal is None:
        raise ValueError("the owner principal does not exist")
    now = _now()
    with immediate_transaction(conn):
        cursor = conn.execute(
            "INSERT INTO accounts (account_ref, telegram_user_id, label, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (mint_opaque_ref("tga_"), telegram_user_id, label, now, now),
        )
        assert cursor.lastrowid is not None  # an INSERT always sets it
        account_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO policy_state (principal_id, account_id, mode, policy_epoch,"
            " include_archived, include_private, include_groups, include_channels, updated_at)"
            " VALUES (?, ?, 'allowlist', 1, 0, 1, 1, 1, ?)",
            (principal[0], account_id, now),
        )
    return account_id
