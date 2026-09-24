import hashlib
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from whatsvault import doctor, keys
from whatsvault.crypto import sealed as S
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ingest import dlq, retention

DAY = 24 * 3600 * 1000


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    return conn


def _quarantine_for_key(conn, key_id):
    pub = X25519PrivateKey.generate().public_key().public_bytes_raw()
    env = S.seal(pub, b"x", recipient_key_id=key_id, event_id_hash=hashlib.sha256(b"e").digest())
    dlq.quarantine(
        conn,
        env,
        failure_class="AEAD_AUTH_FAILED_ISOLATED",
        failure_code="a",
        pipeline_stage="decrypt",
        detail="d",
        now_ms=1,
    )


def test_retire_refuses_while_dlq_references_key(tmp_path):
    conn = _vault(tmp_path)
    _quarantine_for_key(conn, 7)
    with pytest.raises(keys.KeyStillReferenced):
        keys.retire(conn, 7, edge_clear=True)


def test_retire_refuses_when_edge_not_clear(tmp_path):
    with pytest.raises(keys.KeyStillReferenced):
        keys.retire(_vault(tmp_path), 7, edge_clear=False)


def test_retire_ok_when_unreferenced_and_edge_clear(tmp_path):
    keys.retire(_vault(tmp_path), 7, edge_clear=True)  # no raise


def test_retention_assess_bands():
    assert retention.assess(0, 13 * DAY) == "CRITICAL"
    assert retention.assess(0, 11 * DAY) == "HIGH"
    assert retention.assess(0, 8 * DAY) == "WARNING"
    assert retention.assess(0, 3 * DAY) == "OK"
    assert retention.assess(None, 99 * DAY) == "OK"


def test_check_ingest_reports_breaker(tmp_path):
    conn = _vault(tmp_path)
    names = {x["check"] for x in doctor.check_ingest(conn)}
    assert {"dlq_depth", "circuit_breaker"} <= names
    dlq.trip(conn, "boom", 1)
    cb = next(x for x in doctor.check_ingest(conn) if x["check"] == "circuit_breaker")
    assert cb["ok"] is False
