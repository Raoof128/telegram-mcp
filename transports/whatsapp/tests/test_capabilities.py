import pytest
import sqlcipher3

from whatsvault.capabilities import CapabilityError, assert_sqlite_capabilities


def _keyed_conn():
    conn = sqlcipher3.connect(":memory:")
    conn.execute("PRAGMA key = \"x'%s'\"" % ("00" * 32))
    return conn


def test_build_has_sqlcipher_and_all_required_features():
    caps = assert_sqlite_capabilities(_keyed_conn())
    assert caps["sqlcipher"] is True
    assert caps["cipher_version"]
    assert caps["fts5"] and caps["trigram"]
    assert caps["fts_secure_delete"] is True
    assert caps["core_secure_delete"] is True
    assert caps["foreign_keys"] is True


def test_missing_capability_raises(monkeypatch):
    def broken(_conn):
        raise sqlcipher3.OperationalError("no such module: fts5")

    monkeypatch.setattr("whatsvault.capabilities._probe_fts5", broken)
    with pytest.raises(CapabilityError):
        assert_sqlite_capabilities(_keyed_conn())
