"""OAuth refresh tokens: opaque, stored hashed, rotated on every use (comms v0.3 Task D32; A35).

``issue`` mints a token in a family and stores only its SHA-256. ``consume`` spends one: a
token already spent is a replay, so the whole family is revoked and nothing is returned. A
token of another security epoch, past its expiry, or revoked is refused. The raw token is
returned once and never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from comms.core import timeutil
from comms.core.security import security_epoch
from comms.core.storage.db import write_tx

__all__ = ["REFRESH_TTL", "RefreshRow", "consume", "issue", "peek", "revoke_family"]

REFRESH_TTL = timedelta(days=30)


@dataclass(frozen=True)
class RefreshRow:
    family: str
    client_id: str
    subject: str
    scopes: tuple[str, ...]
    resource: str
    expires_at: datetime
    token_hash: str = field(repr=False)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def issue(
    conn: Any,
    *,
    family: str | None,
    client_id: str,
    subject: str,
    scopes: tuple[str, ...],
    resource: str,
    now: datetime,
) -> tuple[str, str]:
    """``(raw token, family)``; a new family when ``family`` is None."""
    token = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    family = family or base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO oauth_refresh_tokens (token_hash, family, client_id, subject, scopes,"
            " resource, security_epoch, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _hash(token),
                family,
                client_id,
                subject,
                json.dumps(sorted(scopes)),
                resource,
                security_epoch(conn),
                timeutil.iso(now + REFRESH_TTL),
                timeutil.iso(now),
            ),
        )
    return token, family


def _row(conn: Any, token: str) -> tuple[Any, ...] | None:
    return conn.execute(  # type: ignore[no-any-return]
        "SELECT family, client_id, subject, scopes, resource, security_epoch, expires_at, used,"
        " revoked, token_hash FROM oauth_refresh_tokens WHERE token_hash = ?",
        (_hash(token),),
    ).fetchone()


def _usable(conn: Any, row: tuple[Any, ...], now: datetime) -> RefreshRow | None:
    family, client_id, subject, scopes, resource, epoch, expires, _used, revoked, digest = row
    expiry = timeutil.instant(expires)
    if revoked or int(epoch) != security_epoch(conn) or now >= expiry:
        return None
    return RefreshRow(
        family, client_id, subject, tuple(json.loads(scopes)), resource, expiry, digest
    )


def peek(conn: Any, token: str, *, now: datetime) -> RefreshRow | None:
    """The token's row if it could still be spent (a spent one revokes its family)."""
    if not isinstance(token, str) or not token:
        return None
    row = _row(conn, token)
    if row is None:
        return None
    if row[7]:  # already spent: a replay revokes everything issued in this family
        revoke_family(conn, row[0])
        return None
    return _usable(conn, row, now)


def consume(conn: Any, token: str, *, now: datetime) -> RefreshRow | None:
    """Spend the token once; a second spend revokes the family (reuse detection)."""
    usable = peek(conn, token, now=now)
    if usable is None:
        return None
    with write_tx(conn):
        cur = conn.execute(
            "UPDATE oauth_refresh_tokens SET used = 1 WHERE token_hash = ? AND used = 0",
            (usable.token_hash,),
        )
    if cur.rowcount != 1:  # lost a race with another spend: that is a replay too
        revoke_family(conn, usable.family)
        return None
    return usable


def revoke_family(conn: Any, family: str) -> None:
    with write_tx(conn):
        conn.execute("UPDATE oauth_refresh_tokens SET revoked = 1 WHERE family = ?", (family,))
