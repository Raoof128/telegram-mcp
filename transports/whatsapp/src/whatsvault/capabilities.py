"""Empirically probe that the installed SQLCipher build has the features the
vault and search subsystems require. We verify by running SQL, never by
trusting a version string. Every probe reflects a control that must actually
take effect (spec 'No fake security').

Ordering matters: PRAGMA foreign_keys cannot change inside a transaction, and
the FTS probes issue DDL that opens one. So the connection-state pragmas are
probed FIRST, on the fresh connection, before any DDL."""


class CapabilityError(RuntimeError):
    pass


def _probe_foreign_keys(conn) -> bool:
    conn.execute("PRAGMA foreign_keys = ON")
    return conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _probe_core_secure_delete(conn) -> bool:
    conn.execute("PRAGMA secure_delete = ON")
    return conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def _probe_fts5(conn) -> bool:
    conn.execute("CREATE VIRTUAL TABLE _cap_fts USING fts5(x)")
    conn.execute("DROP TABLE _cap_fts")
    return True


def _probe_trigram(conn) -> bool:
    conn.execute("CREATE VIRTUAL TABLE _cap_tri USING fts5(x, tokenize='trigram')")
    conn.execute("DROP TABLE _cap_tri")
    return True


def _probe_fts_secure_delete(conn) -> bool:
    conn.execute("CREATE VIRTUAL TABLE _cap_sd USING fts5(x)")
    conn.execute("INSERT INTO _cap_sd(_cap_sd, rank) VALUES('secure-delete', 1)")
    conn.execute("DROP TABLE _cap_sd")
    return True


def _cipher_version(conn) -> str:
    row = conn.execute("PRAGMA cipher_version").fetchone()
    return row[0] if row else ""


def assert_sqlite_capabilities(conn) -> dict:
    version = conn.execute("SELECT sqlite_version()").fetchone()[0]
    cipher_version = _cipher_version(conn)
    if not cipher_version:
        raise CapabilityError("SQLCipher is not active: PRAGMA cipher_version is empty")
    provider_row = conn.execute("PRAGMA cipher_provider").fetchone()
    try:
        # Connection-state pragmas first (no open transaction yet)...
        foreign_keys = _probe_foreign_keys(conn)
        core_secure_delete = _probe_core_secure_delete(conn)
        # ...then the DDL-based feature probes.
        caps = {
            "sqlcipher": True,
            "cipher_version": cipher_version,
            "cipher_provider": provider_row[0] if provider_row else "",
            "fts5": _probe_fts5(conn),
            "trigram": _probe_trigram(conn),
            "fts_secure_delete": _probe_fts_secure_delete(conn),
            "core_secure_delete": core_secure_delete,
            "foreign_keys": foreign_keys,
            "sqlite_version": version,
        }
    except Exception as exc:
        raise CapabilityError(f"SQLite build missing a required capability: {exc}") from exc
    for req in ("fts5", "trigram", "fts_secure_delete", "core_secure_delete", "foreign_keys"):
        if not caps[req]:
            raise CapabilityError(f"required capability absent: {req}")
    return caps
