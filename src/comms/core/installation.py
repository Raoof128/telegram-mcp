"""This installation's stable ``cin_`` ref (comms v0.3 Task B25): minted once, never changed.

Backups bind to it together with the provider identities, so a backup restored elsewhere is
recognised as foreign and needs an explicit ``--adopt`` (Task B27).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core import refs, timeutil
from comms.core.storage.db import write_tx

__all__ = ["installation_ref"]


def installation_ref(conn: Any, *, now: datetime) -> str:
    row = conn.execute("SELECT installation_ref FROM installation WHERE id = 1").fetchone()
    if row is not None:
        return str(row[0])
    ref = refs.mint("installation")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO installation (id, installation_ref, created_at) VALUES (1, ?, ?)",
            (ref, timeutil.iso(now)),
        )
    return ref
