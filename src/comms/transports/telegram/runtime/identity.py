"""Caller identity for the loopback ingress (design §2.2, D6).

``PrincipalContext`` says *who* is calling and nothing else. Grants,
memberships, epochs and egress are authority state: they are read at
``snapshot_authority`` and again at ``revalidate_authority``, never frozen
here, because a grant frozen at ingress would survive a revoke issued while
the call is in flight.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from comms.transports.telegram.storage.owner_state import owner_account

__all__ = ["PrincipalContext", "resolve_principal"]


@dataclass(frozen=True)
class PrincipalContext:
    principal_id: int
    principal_ref: str
    client_id: int
    client_ref: str
    client_kind: str
    account_id: int | None
    account_ref: str | None


def resolve_principal(conn: sqlite3.Connection, client_ref: str) -> PrincipalContext | None:
    """An enabled bearer client's identity, or None."""
    row = conn.execute(
        "SELECT c.id, c.client_ref, c.client_kind, c.enabled, c.auth_kind, p.id, p.principal_ref"
        " FROM mcp_clients c JOIN principals p ON p.id = c.principal_id"
        " WHERE c.client_ref = ?",
        (client_ref,),
    ).fetchone()
    if row is None or not row[3] or row[4] != "bearer":
        return None
    account_id = owner_account(conn, principal_id=int(row[5]))
    account_ref = None
    if account_id is not None:
        account_ref = conn.execute(
            "SELECT account_ref FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()[0]
    return PrincipalContext(
        principal_id=int(row[5]),
        principal_ref=row[6],
        client_id=int(row[0]),
        client_ref=row[1],
        client_kind=row[2],
        account_id=account_id,
        account_ref=account_ref,
    )
