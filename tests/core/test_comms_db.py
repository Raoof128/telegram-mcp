"""comms 5b-4 Task 2: comms.db fails closed (R1, R2, H5) and migrates atomically (G23)."""

import os
import sqlite3

import pytest
import sqlcipher3

from comms.core.storage.db import (
    CommsDbKeyError,
    TransactionIOError,
    io_guard,
    open_comms_db,
    write_tx,
)
from comms.core.storage.migrations import Migration, migrate

KEY = bytes(range(32))
PLAINTEXT_HEADER = b"SQLite format 3\x00"


@pytest.mark.parametrize("key", [b"", b"k" * 31, b"k" * 33, "k" * 32, None], ids=repr)
def test_a_short_or_missing_key_is_refused_before_any_file_exists(tmp_path, key):
    path = tmp_path / "comms.db"
    with pytest.raises(CommsDbKeyError, match="^comms database key is invalid$"):
        open_comms_db(path, key)
    assert not path.exists()


def test_a_new_file_is_encrypted(tmp_path):
    path = tmp_path / "comms.db"
    conn = open_comms_db(path, KEY)
    version = conn.execute("PRAGMA cipher_version").fetchone()[0]
    print(f"cipher_version={version}")
    assert version
    conn.close()
    assert path.read_bytes()[:16] != PLAINTEXT_HEADER


def test_a_wrong_key_fails_closed_at_open(tmp_path):
    path = tmp_path / "comms.db"
    open_comms_db(path, KEY).close()
    with pytest.raises(CommsDbKeyError, match="^comms database key is invalid$"):
        open_comms_db(path, os.urandom(32))


def test_a_keyless_sqlite_open_of_the_file_fails(tmp_path):
    path = tmp_path / "comms.db"
    open_comms_db(path, KEY).close()
    plain = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError):
        plain.execute("SELECT count(*) FROM sqlite_master").fetchone()
    plain.close()


def test_the_key_never_appears_in_errors(tmp_path):
    path = tmp_path / "comms.db"
    open_comms_db(path, KEY).close()
    wrong = os.urandom(32)
    with pytest.raises(CommsDbKeyError) as caught:
        open_comms_db(path, wrong)
    for key in (KEY, wrong):
        for text in (str(caught.value), repr(caught.value)):
            assert key.hex() not in text and key.hex().upper() not in text
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_write_tx_commits_and_rolls_back(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    conn.execute("CREATE TABLE t (x INTEGER)")
    with write_tx(conn):
        conn.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(RuntimeError, match="boom"), write_tx(conn):
        conn.execute("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")
    assert [r[0] for r in conn.execute("SELECT x FROM t")] == [1]
    assert not conn.in_transaction


def test_write_tx_refuses_nesting(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    with write_tx(conn):
        with pytest.raises(RuntimeError, match="nested"), write_tx(conn):
            pass
        assert conn.in_transaction


def test_io_guard_refuses_inside_a_transaction(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    io_guard(conn)
    with write_tx(conn), pytest.raises(TransactionIOError):
        io_guard(conn)


TWO = (
    Migration(1, ("CREATE TABLE a (x INTEGER)",)),
    Migration(2, ("CREATE TABLE b (y INTEGER)", "CREATE INDEX b_y ON b (y)")),
)


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrate_is_rerunnable_and_versioned(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    assert migrate(conn, TWO[:1]) == 1
    assert migrate(conn, TWO) == 2
    assert migrate(conn, TWO) == 2
    assert {"a", "b", "schema_version"} <= _tables(conn)
    assert [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")] == [
        1,
        2,
    ]


BROKEN = (Migration(1, ("CREATE TABLE first (x INTEGER)", "CREATE TABLE this is not sql")),)


def test_failed_migration_does_not_advance_version(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    with pytest.raises(sqlcipher3.dbapi2.OperationalError):
        migrate(conn, BROKEN)
    assert conn.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0
    assert not conn.in_transaction


def test_failed_migration_leaves_no_partial_schema(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    with pytest.raises(sqlcipher3.dbapi2.OperationalError):
        migrate(conn, BROKEN)
    assert "first" not in _tables(conn)


def test_migrations_must_be_ascending_and_contiguous(tmp_path):
    conn = open_comms_db(tmp_path / "comms.db", KEY)
    for bad in ((TWO[1],), (TWO[1], TWO[0]), (TWO[0], TWO[0])):
        with pytest.raises(ValueError, match="migrations"):
            migrate(conn, bad)


def test_lock_contention_raises_after_the_timeout(tmp_path):
    path = tmp_path / "comms.db"
    first = open_comms_db(path, KEY)
    second = sqlcipher3.connect(str(path), isolation_level=None, timeout=0.1)
    second.execute(f"PRAGMA key = \"x'{KEY.hex()}'\"")
    with write_tx(first):
        first.execute("CREATE TABLE t (x INTEGER)")
        with pytest.raises(sqlcipher3.dbapi2.OperationalError, match="locked"):
            second.execute("BEGIN IMMEDIATE")
