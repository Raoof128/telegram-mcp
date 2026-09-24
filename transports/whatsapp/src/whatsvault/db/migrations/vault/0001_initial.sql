CREATE TABLE accounts (
    id TEXT PRIMARY KEY, waba_id TEXT, phone_number_id TEXT NOT NULL, display_phone TEXT
);

CREATE TABLE contacts (
    id TEXT PRIMARY KEY, wa_id TEXT, wa_id_hash TEXT, display_name TEXT, push_name TEXT,
    first_seen_ms INTEGER, last_seen_ms INTEGER
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    type TEXT NOT NULL CHECK (type IN ('dm','group')),
    wa_chat_id TEXT, subject TEXT, last_message_ms INTEGER
);

CREATE TABLE conversation_sources (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual_export','meta_cloud','history_sync')),
    external_identifier TEXT,
    write_capable INTEGER NOT NULL DEFAULT 0 CHECK (write_capable IN (0,1)),
    account_id TEXT REFERENCES accounts(id),
    import_batch_id TEXT
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    sender_contact_id TEXT REFERENCES contacts(id),
    direction TEXT NOT NULL CHECK (direction IN ('in','out')),
    ts_lower_ms INTEGER NOT NULL,
    ts_upper_ms_exclusive INTEGER NOT NULL,
    ts_precision TEXT NOT NULL CHECK (ts_precision IN ('ms','s','min','day')),
    ts_ingested_ms INTEGER,
    tz_name TEXT,
    tz_basis TEXT CHECK (tz_basis IN ('provider','explicit_import_setting','inferred','unknown')),
    type TEXT NOT NULL,
    text_original TEXT,
    reply_to_wamid TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('cloud_api','business_app_echo','history_sync','manual_export')),
    window_eligible INTEGER NOT NULL DEFAULT 0 CHECK (window_eligible IN (0,1)),
    wamid TEXT,
    import_fingerprint TEXT,
    edited_at_ms INTEGER,
    deleted_at_ms INTEGER,
    delivery_rank INTEGER NOT NULL DEFAULT 0 CHECK (delivery_rank BETWEEN 0 AND 3),
    failed_at_ms INTEGER,
    CHECK (ts_upper_ms_exclusive > ts_lower_ms)
);
CREATE UNIQUE INDEX ux_messages_wamid ON messages(account_id, wamid) WHERE wamid IS NOT NULL;
CREATE UNIQUE INDEX ux_messages_import_fp ON messages(import_fingerprint) WHERE import_fingerprint IS NOT NULL;
CREATE INDEX ix_messages_window ON messages(conversation_id, direction, window_eligible);

CREATE TABLE message_revisions (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 0),
    event_id TEXT,
    text_original TEXT,
    ts_lower_ms INTEGER,
    UNIQUE(message_id, revision_number)
);

CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    provider_media_id TEXT,
    provider_sha256 TEXT,
    mime TEXT,
    size INTEGER CHECK (size IS NULL OR size >= 0),
    retrieval_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (retrieval_state IN ('PENDING','FETCHED','TEMPORARILY_FAILED','UNAVAILABLE','BACKFILLED',
                                   'MEDIA_PLACEHOLDER','FILE_PRESENT','FILE_NOT_INCLUDED_IN_EXPORT','FILE_REFERENCE_BROKEN')),
    quarantine_state TEXT NOT NULL DEFAULT 'quarantined',
    retrieved_at_ms INTEGER,
    last_attempt_ms INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code TEXT,
    storage_path TEXT
);
CREATE UNIQUE INDEX ux_attachments_media ON attachments(message_id, provider_media_id) WHERE provider_media_id IS NOT NULL;

CREATE TABLE message_status_events (
    id TEXT PRIMARY KEY,
    wamid TEXT NOT NULL,
    message_internal_id TEXT,
    status TEXT NOT NULL,
    provider_ts_ms INTEGER NOT NULL,
    recipient_id TEXT,
    raw_payload_sha256 TEXT
);

CREATE TABLE ingest_events (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    external_event_id TEXT,
    semantic_event_key TEXT NOT NULL,
    family TEXT NOT NULL,
    provider_ts_ms INTEGER,
    received_at_ms INTEGER NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    raw_payload BLOB NOT NULL,
    parser_version INTEGER NOT NULL
);
CREATE UNIQUE INDEX ux_ingest_semantic ON ingest_events(provider, semantic_event_key);

CREATE TRIGGER trg_messages_evidence_immutable
BEFORE UPDATE OF account_id, conversation_id, sender_contact_id, direction,
                 ts_lower_ms, ts_upper_ms_exclusive, ts_precision, ts_ingested_ms,
                 tz_name, tz_basis, type, text_original, reply_to_wamid, origin,
                 window_eligible, wamid, import_fingerprint
ON messages
BEGIN SELECT RAISE(ABORT, 'message evidence fields are immutable'); END;

CREATE TRIGGER trg_status_evidence_immutable
BEFORE UPDATE OF wamid, status, provider_ts_ms, recipient_id, raw_payload_sha256
ON message_status_events
BEGIN SELECT RAISE(ABORT, 'status evidence is immutable (only message_internal_id backlink may change)'); END;

CREATE TRIGGER trg_revisions_immutable
BEFORE UPDATE ON message_revisions
BEGIN SELECT RAISE(ABORT, 'message_revisions are immutable evidence'); END;

CREATE TRIGGER trg_ingest_immutable
BEFORE UPDATE ON ingest_events
BEGIN SELECT RAISE(ABORT, 'ingest_events are write-once evidence'); END;
