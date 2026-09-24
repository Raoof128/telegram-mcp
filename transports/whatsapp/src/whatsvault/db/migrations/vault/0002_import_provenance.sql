-- Phase 1b: import provenance (ledger #25 self/direction, #26 sender link,
-- #29 immutability with sanctioned mutation paths, #30 durable source artefact).

CREATE TABLE import_batches (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual_export')),
    source_sha256 TEXT NOT NULL,
    source_artifact_path TEXT,
    source_artifact_sha256 TEXT,
    original_filename TEXT,
    declared_date_format TEXT NOT NULL CHECK (declared_date_format IN ('DMY','MDY','YMD')),
    declared_timezone TEXT NOT NULL,
    self_participant_label TEXT,
    parser_family TEXT,
    parser_version INTEGER,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    imported_at_ms INTEGER,
    message_count INTEGER,
    system_event_count INTEGER,
    undone_at_ms INTEGER
);

CREATE TABLE import_participants (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_conversation_id TEXT,
    raw_display_name TEXT NOT NULL,
    normalised_display_name TEXT,
    role TEXT,
    linked_contact_id TEXT REFERENCES contacts(id),
    link_state TEXT NOT NULL DEFAULT 'UNLINKED'
        CHECK (link_state IN ('UNLINKED','LINKED_EXPLICIT','LINK_REVOKED'))
);

CREATE TABLE message_import_observations (
    batch_id TEXT NOT NULL REFERENCES import_batches(id),
    message_id TEXT NOT NULL REFERENCES messages(id),
    sender_import_participant_id TEXT REFERENCES import_participants(id),
    source_ordinal INTEGER NOT NULL,
    source_start_offset INTEGER,
    source_end_offset INTEGER,
    source_fingerprint TEXT,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    parser_version INTEGER,
    PRIMARY KEY (batch_id, message_id),
    UNIQUE (batch_id, source_ordinal)
);
CREATE INDEX ix_observations_message ON message_import_observations(message_id);

-- #29 immutability with sanctioned mutation paths only.
-- Batch: identity/declared/artefact fields frozen; undone_at_ms/counters remain settable.
CREATE TRIGGER trg_import_batches_immutable
BEFORE UPDATE OF id, source_kind, source_sha256, source_artifact_path, source_artifact_sha256,
                 original_filename, declared_date_format, declared_timezone, self_participant_label
ON import_batches
BEGIN SELECT RAISE(ABORT, 'import_batches identity/declared/artefact fields are immutable'); END;

-- Participant: only link_state / linked_contact_id may transition; provisional identity frozen.
CREATE TRIGGER trg_import_participants_identity_immutable
BEFORE UPDATE OF id, import_batch_id, raw_display_name, normalised_display_name
ON import_participants
BEGIN SELECT RAISE(ABORT, 'import_participant provisional identity is immutable (only link_state/linked_contact_id change)'); END;

-- Observation: write-once; undo DELETEs, never UPDATEs.
CREATE TRIGGER trg_import_observations_immutable
BEFORE UPDATE ON message_import_observations
BEGIN SELECT RAISE(ABORT, 'import observations are write-once (undo deletes, never updates)'); END;
