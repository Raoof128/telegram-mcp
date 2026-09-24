"""Local DLQ + key-health decrypt taxonomy + circuit-breaker (ledger #37/#38/#41).

Classification uses KEY HEALTH, not batch-cohort size: a lone AEAD failure on a key
that has not decrypted anything is SYSTEMIC (circuit-break, no ACK), never poison —
this is the single-message-batch fix. Quarantine stores key/crypto metadata +
ciphertext_sha256 (for key retirement) and the sealed envelope for retry, but never a
plaintext hash and never a payload excerpt."""

from .. import ids
from ..crypto import sealed as _sealed

_DETAIL_CAP = 200


def classify_decrypt_error(exc, *, key_healthy: bool) -> str:
    if isinstance(exc, _sealed.KeyUnavailable):
        return "KEY_UNAVAILABLE"
    if isinstance(exc, _sealed.BadEnvelope):
        return "POISON_MALFORMED"
    if isinstance(exc, _sealed.AeadAuthFailed):
        return "AEAD_AUTH_FAILED_ISOLATED" if key_healthy else "AEAD_AUTH_FAILED_SYSTEMIC"
    return "AEAD_AUTH_FAILED_SYSTEMIC"  # unknown decrypt error -> conservative (no ACK)


def quarantine(vault_conn, envelope, *, failure_class, failure_code, pipeline_stage, detail, now_ms) -> None:
    try:
        hdr = _sealed.parse_header(envelope)
    except _sealed.BadEnvelope:
        hdr = {}
    eidh = hdr.get("event_id_hash")
    vault_conn.execute(
        "INSERT INTO ingest_dlq(id, event_id_hash, envelope, failure_class, failure_code, "
        "pipeline_stage, recipient_key_id, crypto_version, envelope_version, ciphertext_sha256, "
        "parser_version, attempt_count, sanitised_detail, first_seen_ms, last_attempt_ms) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (
            ids.new_id("dlq"),
            eidh.hex() if eidh else None,
            envelope,
            failure_class,
            failure_code,
            pipeline_stage,
            hdr.get("recipient_key_id"),
            hdr.get("crypto_version"),
            hdr.get("envelope_version"),
            hdr.get("ciphertext_sha256"),
            1,
            (detail or "")[:_DETAIL_CAP],
            now_ms,
            now_ms,
        ),
    )
    vault_conn.commit()


def state(vault_conn) -> str:
    return vault_conn.execute("SELECT circuit_state FROM ingest_state WHERE id=1").fetchone()[0]


def trip(vault_conn, reason, now_ms) -> None:
    vault_conn.execute(
        "UPDATE ingest_state SET circuit_state='OPEN', tripped_at_ms=?, reason=? WHERE id=1",
        (now_ms, (reason or "")[:_DETAIL_CAP]),
    )
    vault_conn.commit()


def reset(vault_conn) -> None:
    vault_conn.execute(
        "UPDATE ingest_state SET circuit_state='CLOSED', tripped_at_ms=NULL, reason=NULL WHERE id=1"
    )
    vault_conn.commit()


def references_key(vault_conn, recipient_key_id) -> int:
    return vault_conn.execute(
        "SELECT COUNT(*) FROM ingest_dlq WHERE recipient_key_id=?", (recipient_key_id,)
    ).fetchone()[0]
