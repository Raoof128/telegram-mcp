import os

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ingest import dlq
from whatsvault.ops import health, structlog


def _dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return v, c


def test_clean_vault_is_ok(tmp_path):
    v, c = _dbs(tmp_path)
    s = health.status(v, c)
    assert s["ok"] is True and s["summary"]["circuit_state"] == "CLOSED"


def test_tripped_breaker_makes_status_not_ok(tmp_path):
    v, c = _dbs(tmp_path)
    dlq.trip(v, "boom", 1)
    s = health.status(v, c)
    assert s["ok"] is False and s["summary"]["circuit_state"] == "OPEN"


def test_structlog_refuses_content():
    assert structlog.event({"tool": "search", "outcome": "ok"})["tool"] == "search"
    with pytest.raises(structlog.ContentInLogError):
        structlog.event({"body": "secret message"})
    with pytest.raises(structlog.ContentInLogError):
        structlog.event({"x": {"_wv_untrusted": True, "text": "hi"}})
