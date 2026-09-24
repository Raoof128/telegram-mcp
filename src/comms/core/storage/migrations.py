"""Append-only numbered migrations for comms.db (design §2, G23).

Each pending migration runs in ONE ``BEGIN IMMEDIATE`` with its version row, so
a failure leaves neither partial schema nor an advanced version (measured, M11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from comms.core.storage.db import write_tx

__all__ = ["MIGRATIONS", "Migration", "migrate"]


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = ()


def migrate(conn: Any, migrations: tuple[Migration, ...] = MIGRATIONS) -> int:
    """Apply every migration not yet recorded; return the resulting version."""
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("migrations must be numbered 1..n in order")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    done = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    for migration in migrations:
        if migration.version in done:
            continue
        with write_tx(conn):
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (migration.version,))
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return int(row[0] or 0)
