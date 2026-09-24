"""Numbered, transactional migration runner. This package's __init__ IS the
runner (a sibling migrations.py would be shadowed by this package directory);
the numbered SQL assets live under vault/ and control/ within this package.

Each migration runs inside a BEGIN/COMMIT and bumps PRAGMA user_version
atomically; a failure rolls back and leaves the version unchanged."""

import contextlib
from importlib import resources

MIGRATIONS: dict[str, list[tuple[int, str]]] = {
    "vault": [
        (1, "vault/0001_initial.sql"),
        (2, "vault/0002_import_provenance.sql"),
        (3, "vault/0003_search.sql"),
        (4, "vault/0004_mcp_visibility.sql"),
        (5, "vault/0005_ingest_ops.sql"),
        (6, "vault/0006_status_callback.sql"),
    ],
    "control": [
        (1, "control/0001_initial.sql"),
        (2, "control/0002_device_agreement.sql"),
        (3, "control/0003_phase5.sql"),
        (4, "control/0004_oauth.sql"),
        (5, "control/0005_oauth_families.sql"),
    ],
}


def user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _read_asset(relpath: str) -> str:
    return resources.files("whatsvault.db.migrations").joinpath(relpath).read_text(encoding="utf-8")


def migrate(conn, lane: str) -> int:
    if lane not in MIGRATIONS:
        raise ValueError(f"unknown migration lane: {lane!r}")
    for version, relpath in MIGRATIONS[lane]:
        if version <= user_version(conn):
            continue
        sql = _read_asset(relpath)
        script = f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            with contextlib.suppress(Exception):
                conn.execute("ROLLBACK")
            raise
    return user_version(conn)
