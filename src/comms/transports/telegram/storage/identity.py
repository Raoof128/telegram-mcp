"""Identity bootstrap (design §3.8; spec §10.4, §10.5, §12.2)."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import UTC, datetime

from comms.core.opaque import mint_opaque_ref
from comms.core.storage.db import write_tx
from comms.transports.telegram.storage.settings import get_setting, put_setting

__all__ = [
    "OWNER_PRINCIPAL",
    "active_account",
    "bind_login",
    "ensure_account",
    "ensure_owner_principal",
]

OWNER_PRINCIPAL = "local_single_principal"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_owner_principal(conn: sqlite3.Connection, *, privacy_key: bytes) -> int:
    key = hmac.new(privacy_key, OWNER_PRINCIPAL.encode(), hashlib.sha256).hexdigest()
    row = conn.execute("SELECT id FROM principals WHERE principal_key = ?", (key,)).fetchone()
    if row is not None:
        return int(row[0])
    with write_tx(conn):
        cursor = conn.execute(
            "INSERT INTO principals (principal_ref, principal_key, auth_mode, label, created_at)"
            " VALUES (?, ?, 'local', 'owner', ?)",
            (mint_opaque_ref("prn_"), key, _now()),
        )
    assert cursor.lastrowid is not None  # an INSERT always sets it
    return int(cursor.lastrowid)


def _create_account_in_tx(
    conn: sqlite3.Connection, telegram_user_id: int, label: str | None, now: str
) -> int:
    principal = conn.execute("SELECT id FROM principals ORDER BY id LIMIT 1").fetchone()
    if principal is None:
        raise ValueError("the owner principal does not exist")
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


def ensure_account(conn: sqlite3.Connection, *, telegram_user_id: int, label: str | None) -> int:
    row = conn.execute(
        "SELECT id FROM accounts WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()
    if row is not None:
        return int(row[0])
    with write_tx(conn):
        return _create_account_in_tx(conn, telegram_user_id, label, _now())


def active_account(conn: sqlite3.Connection) -> int | None:
    """The account the owner currently uses (comms v0.3 B16): the pointer, else the only one."""
    ref = get_setting(conn, "telegram.active_account")
    if ref:
        row = conn.execute("SELECT id FROM accounts WHERE account_ref = ?", (ref,)).fetchone()
        return None if row is None else int(row[0])
    rows = conn.execute("SELECT id FROM accounts LIMIT 2").fetchall()
    return int(rows[0][0]) if len(rows) == 1 else None


def bind_login(
    conn: sqlite3.Connection, *, telegram_user_id: int, new_account: bool, now: str
) -> str:
    """Bind a successful login: a new session generation and security epoch (B16).

    A different Telegram user than the active account is refused unless ``new_account``;
    with it, the pointer moves and the old account's rows stay for history.
    """
    current = active_account(conn)
    with write_tx(conn):
        row = conn.execute(
            "SELECT id, account_ref FROM accounts WHERE telegram_user_id = ?", (telegram_user_id,)
        ).fetchone()
        if current is not None and (row is None or int(row[0]) != current) and not new_account:
            raise ValueError("a different Telegram account signed in; pass new_account to switch")
        if row is None:
            account_id = _create_account_in_tx(conn, telegram_user_id, None, now)
            ref = conn.execute(
                "SELECT account_ref FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
        else:
            ref = row[1]
        generation = int(get_setting(conn, "telegram.session_generation")) + 1
        put_setting(conn, "telegram.active_account", ref, now=now)
        put_setting(conn, "telegram.session_generation", generation, now=now)
        put_setting(conn, "telegram.session_state", "active", now=now)
        put_setting(conn, "telegram.remote_revoke", "none", now=now)
        conn.execute(
            "UPDATE security_state SET security_epoch = security_epoch + 1, updated_at = ?"
            " WHERE singleton_id = 1",
            (now,),
        )
    return str(ref)
