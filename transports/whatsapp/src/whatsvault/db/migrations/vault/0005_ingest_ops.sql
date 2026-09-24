-- Phase 3: ingest operational state (ledger #38 DLQ, #41 circuit-breaker).
-- ingest_dlq carries key/crypto metadata + ciphertext_sha256 for key retirement (#39);
-- it retains the sealed envelope (ciphertext) for retry and has NO payload_sha256
-- column (a decrypt failure has no plaintext, #38). Diagnostics are bounded, no payload.
CREATE TABLE ingest_dlq (
    id TEXT PRIMARY KEY,
    event_id_hash TEXT,
    envelope BLOB NOT NULL,
    failure_class TEXT NOT NULL,
    failure_code TEXT,
    pipeline_stage TEXT,
    recipient_key_id INTEGER,
    crypto_version INTEGER,
    envelope_version INTEGER,
    ciphertext_sha256 TEXT,
    parser_version INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    sanitised_detail TEXT,
    first_seen_ms INTEGER,
    last_attempt_ms INTEGER
);
CREATE INDEX ix_dlq_key ON ingest_dlq(recipient_key_id);

-- Single-row circuit-breaker: tripped by SYSTEMIC decrypt failure / disk-full; reset by CLI.
CREATE TABLE ingest_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    circuit_state TEXT NOT NULL DEFAULT 'CLOSED' CHECK (circuit_state IN ('CLOSED','OPEN')),
    tripped_at_ms INTEGER,
    reason TEXT
);
INSERT INTO ingest_state(id, circuit_state) VALUES (1, 'CLOSED');
