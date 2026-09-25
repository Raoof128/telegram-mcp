"""The security epoch and the owner's account: plain reads, free of the policy engine.

Comms v0.3 retired the policy evaluator from production (A3). The daemon's lock, lease
and identity paths still need these two reads, so they live here, where importing them
does not reach ``authority.policy``; ``authority_view`` re-exports them for the retained
read path.
"""

from __future__ import annotations

import sqlite3

__all__ = ["load_security", "owner_account"]


def load_security(conn: sqlite3.Connection) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT security_epoch, locked FROM security_state WHERE singleton_id = 1"
    ).fetchone()
    if row is None:
        raise ValueError("security_state singleton is missing")
    return int(row[0]), bool(row[1])


def owner_account(conn: sqlite3.Connection, *, principal_id: int) -> int | None:
    """The owner's active account, if the owner has policy for it; else None (unconfigured).

    comms v0.3 B16: with several accounts (an explicit --new-account switch), the active
    pointer decides; with none set, exactly one policy row is required.
    """
    from comms.transports.telegram.storage.identity import active_account

    rows = [
        int(r[0])
        for r in conn.execute(
            "SELECT account_id FROM policy_state WHERE principal_id = ?", (principal_id,)
        ).fetchall()
    ]
    active = active_account(conn)
    if active is not None:
        return active if active in rows else None
    return rows[0] if len(rows) == 1 else None
