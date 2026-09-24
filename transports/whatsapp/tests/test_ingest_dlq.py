import hashlib
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from whatsvault.crypto import sealed as S
from whatsvault.crypto.sealed import AeadAuthFailed, BadEnvelope, KeyUnavailable
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ingest import dlq


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    return conn


def test_classify_uses_key_health_not_cohort():
    assert dlq.classify_decrypt_error(KeyUnavailable(), key_healthy=True) == "KEY_UNAVAILABLE"
    assert dlq.classify_decrypt_error(BadEnvelope(), key_healthy=True) == "POISON_MALFORMED"
    assert dlq.classify_decrypt_error(AeadAuthFailed(), key_healthy=True) == "AEAD_AUTH_FAILED_ISOLATED"
    # single-message-batch case (#37): a cold key -> SYSTEMIC, never poison
    assert dlq.classify_decrypt_error(AeadAuthFailed(), key_healthy=False) == "AEAD_AUTH_FAILED_SYSTEMIC"


def test_reaches_version_5(tmp_path):
    assert M.user_version(_vault(tmp_path)) >= 5


def test_quarantine_stores_metadata_no_payload(tmp_path):
    conn = _vault(tmp_path)
    pub = X25519PrivateKey.generate().public_key().public_bytes_raw()
    env = S.seal(
        pub,
        b"SECRET-PAYLOAD",
        recipient_key_id=5,
        event_id_hash=hashlib.sha256(b"e").digest(),
        crypto_version=2,
    )
    dlq.quarantine(
        conn,
        env,
        failure_class="AEAD_AUTH_FAILED_ISOLATED",
        failure_code="poison",
        pipeline_stage="decrypt",
        detail="stage=decrypt",
        now_ms=100,
    )
    row = conn.execute(
        "SELECT recipient_key_id, crypto_version, envelope_version, ciphertext_sha256, "
        "sanitised_detail, envelope FROM ingest_dlq"
    ).fetchone()
    assert row["recipient_key_id"] == 5 and row["crypto_version"] == 2 and row["envelope_version"] == 1
    assert len(row["ciphertext_sha256"]) == 64
    assert "SECRET-PAYLOAD" not in row["sanitised_detail"] and len(row["sanitised_detail"]) <= 200
    assert bytes(row["envelope"]) == env  # ciphertext retained for retry


def test_no_payload_sha256_column(tmp_path):
    conn = _vault(tmp_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ingest_dlq)").fetchall()]
    assert "payload_sha256" not in cols and "ciphertext_sha256" in cols and "recipient_key_id" in cols


def test_circuit_breaker_trip_reset(tmp_path):
    conn = _vault(tmp_path)
    assert dlq.state(conn) == "CLOSED"
    dlq.trip(conn, "systemic decrypt failure", 123)
    assert dlq.state(conn) == "OPEN"
    dlq.reset(conn)
    assert dlq.state(conn) == "CLOSED"
