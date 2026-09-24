import os

import pytest
import sqlcipher3

from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _conn(tmp_path, name="v.db"):
    return C.open_db(str(tmp_path / name), os.urandom(32))


def test_migrate_is_idempotent_and_sets_version(tmp_path):
    conn = _conn(tmp_path)
    v1 = M.migrate(conn, "vault")
    v2 = M.migrate(conn, "vault")
    assert v1 == v2 == max(v for v, _ in M.MIGRATIONS["vault"])


def test_partial_failure_does_not_bump_version(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    good_max = max(v for v, _ in M.MIGRATIONS["vault"])
    M.migrate(conn, "vault")
    monkeypatch.setitem(M.MIGRATIONS, "vault", M.MIGRATIONS["vault"] + [(good_max + 1, "vault/_bogus.sql")])
    real_read = M._read_asset
    monkeypatch.setattr(
        M, "_read_asset", lambda p: "CREATE TABLE bad(;" if p.endswith("_bogus.sql") else real_read(p)
    )
    # sqlcipher3 ships its own DB-API exception tree: sqlcipher3.Error is NOT a
    # subclass of sqlite3.Error, so catching the stdlib type here would never match.
    with pytest.raises(sqlcipher3.Error):
        M.migrate(conn, "vault")
    assert M.user_version(conn) == good_max
