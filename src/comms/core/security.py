"""The comms security epoch (comms v0.3 Task D9; A30).

One forward-only counter in comms.db. A revocation bumps it; every ctx_ handle and cur_ cursor
records the epoch it was made in and is stale in any other.
"""

from __future__ import annotations

from typing import Any

from comms.core.storage.db import write_tx

__all__ = ["bump_security_epoch", "security_epoch"]


def security_epoch(conn: Any) -> int:
    return int(conn.execute("SELECT epoch FROM security_epoch WHERE id = 1").fetchone()[0])


def bump_security_epoch(conn: Any) -> int:
    with write_tx(conn):
        conn.execute("UPDATE security_epoch SET epoch = epoch + 1 WHERE id = 1")
    return security_epoch(conn)
