CREATE TABLE approval_devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    public_key BLOB NOT NULL,
    key_algorithm TEXT NOT NULL DEFAULT 'P-256' CHECK (key_algorithm IN ('P-256')),
    key_encoding TEXT NOT NULL DEFAULT 'sec1-uncompressed' CHECK (key_encoding IN ('sec1-uncompressed')),
    created_at_ms INTEGER,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED'))
);

CREATE TABLE drafts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    phone_number_id TEXT NOT NULL,
    recipient_id TEXT,
    recipient_wa_id TEXT,
    recipient_display_snapshot TEXT,
    body_bytes BLOB,
    body_sha256 BLOB CHECK (body_sha256 IS NULL OR length(body_sha256) = 32),
    kind TEXT NOT NULL CHECK (kind IN ('text','template','mark_read')),
    template_id TEXT,
    template_params_sha256 BLOB CHECK (template_params_sha256 IS NULL OR length(template_params_sha256) = 32),
    attachments_digest BLOB CHECK (attachments_digest IS NULL OR length(attachments_digest) = 32),
    reply_to_wamid TEXT,
    nonce BLOB UNIQUE CHECK (nonce IS NULL OR length(nonce) = 32),
    created_at_ms INTEGER,
    expires_at_ms INTEGER,
    created_by TEXT CHECK (created_by IS NULL OR created_by IN ('mcp','scheduler')),
    state TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (state IN ('DRAFT','PENDING_APPROVAL','APPROVAL_RECEIVED','SENDING','SUBMITTING',
                         'SUBMITTED','EXPIRED','REJECTED','CANCELLED','FAILED',
                         'INDETERMINATE','ABANDONED_INDETERMINATE'))
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES drafts(id),
    device_id TEXT NOT NULL REFERENCES approval_devices(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
    signature BLOB CHECK (signature IS NULL OR length(signature) = 64),
    envelope BLOB,
    received_at_ms INTEGER,
    nonce BLOB CHECK (nonce IS NULL OR length(nonce) = 32),
    UNIQUE(draft_id, device_id, decision, nonce)
);

CREATE TABLE approval_nonces (
    nonce BLOB PRIMARY KEY CHECK (length(nonce) = 32),
    consumed_by TEXT,
    consumed_at_ms INTEGER
);

CREATE TABLE send_attempts (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES drafts(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('SUBMITTING','SUBMITTED','FAILED','INDETERMINATE','ABANDONED_INDETERMINATE')),
    wamid TEXT,
    error_code TEXT,
    biz_opaque_callback_data TEXT,
    created_at_ms INTEGER,
    updated_at_ms INTEGER
);

CREATE TABLE capability_grants (
    capability_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES approval_devices(id),
    account_id TEXT,
    conversation_id TEXT,
    action TEXT NOT NULL,
    created_at_ms INTEGER,
    expires_at_ms INTEGER,
    max_actions INTEGER CHECK (max_actions IS NULL OR max_actions >= 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    nonce BLOB CHECK (nonce IS NULL OR length(nonce) = 32),
    signature BLOB CHECK (signature IS NULL OR length(signature) = 64),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED'))
);

CREATE TABLE conversation_windows (
    conversation_id TEXT PRIMARY KEY,
    last_inbound_ms INTEGER NOT NULL DEFAULT 0 CHECK (last_inbound_ms >= 0)
);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    ts_ms INTEGER NOT NULL
);

CREATE TRIGGER trg_draft_freeze
BEFORE UPDATE OF body_bytes, body_sha256, recipient_wa_id, recipient_id, account_id,
                 phone_number_id, nonce, expires_at_ms, kind, template_id,
                 template_params_sha256, attachments_digest, reply_to_wamid
ON drafts
WHEN OLD.state <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'draft core fields freeze once state leaves DRAFT'); END;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
