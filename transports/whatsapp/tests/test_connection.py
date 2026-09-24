import os

import pytest
import sqlcipher3

from whatsvault.crypto.keystore import MemoryKeyStore
from whatsvault.db import connection as C


def test_provision_write_then_open_existing(tmp_path):
    ks = MemoryKeyStore()
    p = str(tmp_path / "v.db")
    conn = C.provision_db("vault", p, ks)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t(x) VALUES('hello')")
    conn.commit()
    conn.close()

    conn2 = C.open_existing("vault", p, ks)
    assert conn2.execute("SELECT x FROM t").fetchone()[0] == "hello"
    assert conn2.execute("PRAGMA secure_delete").fetchone()[0] == 1
    conn2.close()


def test_open_db_eagerly_rejects_wrong_key(tmp_path):
    p = str(tmp_path / "v.db")
    good = os.urandom(32)
    conn = C.open_db(p, good)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t(x) VALUES('secret')")
    conn.commit()
    conn.close()

    bad = os.urandom(32)
    with pytest.raises(sqlcipher3.DatabaseError):
        C.open_db(p, bad)
