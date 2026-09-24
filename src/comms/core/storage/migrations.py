"""Append-only numbered migrations for comms.db (design §2, G23).

Each pending migration runs in ONE ``BEGIN IMMEDIATE`` with its version row, so
a failure leaves neither partial schema nor an advanced version (measured, M11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from comms.core.storage.db import write_tx

__all__ = ["MIGRATIONS", "SCHEMA_V1", "SCHEMA_V2", "SCHEMA_V3", "Migration", "migrate"]


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]
    # Runs with foreign keys off (set outside the transaction) and must leave
    # ``PRAGMA foreign_key_check`` empty: the 5b-3 table-rebuild method.
    rebuild: bool = False


_SCHEMA_V1_SQL = """
CREATE TABLE locations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE recipients (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE delivery_identities (id INTEGER PRIMARY KEY,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), identity TEXT NOT NULL,
  UNIQUE (transport, identity));
CREATE TABLE destinations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport = 'telegram'), platform_identity TEXT NOT NULL,
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  display_name TEXT NOT NULL, capabilities TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE INDEX destinations_location ON destinations (location_id);
CREATE UNIQUE INDEX destinations_one_enabled_per_identity ON destinations (identity_id) WHERE enabled = 1;
CREATE TABLE contact_points (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), platform_identity TEXT NOT NULL,
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), opted_out_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX contact_points_recipient ON contact_points (recipient_id);
CREATE UNIQUE INDEX contact_points_one_enabled_per_transport ON contact_points (recipient_id, transport) WHERE enabled = 1;
CREATE UNIQUE INDEX contact_points_one_enabled_per_identity ON contact_points (identity_id) WHERE enabled = 1;
CREATE TABLE location_members (location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
  recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT, PRIMARY KEY (location_id, recipient_id));
CREATE INDEX location_members_recipient ON location_members (recipient_id);
CREATE TABLE audiences (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audience_members (id INTEGER PRIMARY KEY,
  audience_id INTEGER NOT NULL REFERENCES audiences(id) ON DELETE RESTRICT,
  member_location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
  member_audience_id INTEGER REFERENCES audiences(id) ON DELETE RESTRICT,
  member_destination_id INTEGER REFERENCES destinations(id) ON DELETE RESTRICT,
  member_recipient_id INTEGER REFERENCES recipients(id) ON DELETE RESTRICT,
  CHECK ((member_location_id IS NOT NULL) + (member_audience_id IS NOT NULL)
       + (member_destination_id IS NOT NULL) + (member_recipient_id IS NOT NULL) = 1),
  CHECK (member_audience_id IS NULL OR member_audience_id <> audience_id));
CREATE UNIQUE INDEX audience_members_unique ON audience_members (audience_id,
  IFNULL(member_location_id,0), IFNULL(member_audience_id,0), IFNULL(member_destination_id,0), IFNULL(member_recipient_id,0));
CREATE TABLE campaigns (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  lifecycle TEXT NOT NULL CHECK (lifecycle IN ('DRAFT','READY','SCHEDULED','SENDING','COMPLETE','CANCELLED')),
  content TEXT NOT NULL, targets TEXT NOT NULL, options TEXT NOT NULL,
  summary TEXT CHECK (summary IS NULL OR summary IN ('IN_PROGRESS','INDETERMINATE','SENT','PARTIAL','CANCELLED','FAILED')),
  current_generation_id INTEGER REFERENCES generations(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK ((summary IS NULL) = (current_generation_id IS NULL)));
CREATE TABLE generations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL, send_at TEXT NOT NULL, content TEXT NOT NULL,
  snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64),
  status TEXT NOT NULL CHECK (status IN ('active','discarded')));
CREATE INDEX generations_campaign ON generations (campaign_id);
CREATE TABLE delivery_jobs (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')),
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) = 64),
  payload BLOB, payload_digest TEXT, skip_reason TEXT,
  state TEXT NOT NULL CHECK (state IN ('PENDING','IN_FLIGHT','ACCEPTED','DELIVERED','FAILED_TRANSIENT',
    'FAILED_PERMANENT','OUTCOME_UNKNOWN','CANCELLED','SKIPPED_PLATFORM_POLICY','SKIPPED_REVALIDATION')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  UNIQUE (generation_id, transport, identity_id),
  CHECK ((payload IS NULL) = (payload_digest IS NULL)),
  CHECK ((payload IS NULL) = (skip_reason IS NOT NULL)),
  CHECK ((state = 'SKIPPED_PLATFORM_POLICY') = (payload IS NULL)),
  CHECK (payload_digest IS NULL OR (length(payload_digest) = 64 AND payload_digest NOT GLOB '*[^0-9a-f]*')));
CREATE TABLE job_origins (id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  endpoint_ref TEXT NOT NULL, path TEXT NOT NULL,          -- TG-JCS-v1 array of refs, target first, endpoint last
  CHECK (json_valid(path) AND json_type(path) = 'array' AND json_array_length(path) >= 1),
  CHECK (json_extract(path, '$[#-1]') = endpoint_ref));
CREATE INDEX job_origins_job ON job_origins (job_id);
CREATE TABLE delivery_attempts (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  job_id INTEGER NOT NULL REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1), started_at TEXT NOT NULL, finished_at TEXT,
  outcome TEXT CHECK (outcome IS NULL OR outcome IN ('ACCEPTED','DELIVERED','FAILED_TRANSIENT','FAILED_PERMANENT','OUTCOME_UNKNOWN')),
  provider_message_ref TEXT, UNIQUE (job_id, attempt_no),
  CHECK ((finished_at IS NULL) = (outcome IS NULL)));
CREATE INDEX delivery_attempts_provider_ref ON delivery_attempts (provider_message_ref) WHERE provider_message_ref IS NOT NULL;
CREATE TABLE provider_events (id INTEGER PRIMARY KEY,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), provider_event_ref TEXT NOT NULL,
  provider_message_ref TEXT NOT NULL,
  job_id INTEGER REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  attempt_id INTEGER REFERENCES delivery_attempts(id) ON DELETE RESTRICT,
  reported_status TEXT NOT NULL CHECK (reported_status IN ('ACCEPTED','DELIVERED','FAILED_PERMANENT')),
  disposition TEXT NOT NULL CHECK (disposition IN ('applied','recorded','refused','pending_match')),
  received_at TEXT NOT NULL, UNIQUE (transport, provider_event_ref),
  CHECK (disposition <> 'pending_match' OR attempt_id IS NULL),
  CHECK (disposition NOT IN ('applied','recorded') OR attempt_id IS NOT NULL));
CREATE INDEX provider_events_pending ON provider_events (transport, provider_message_ref) WHERE disposition = 'pending_match';
CREATE TABLE campaign_events (event_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_ref TEXT NOT NULL UNIQUE,
  campaign_ref TEXT, event_type TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL);
-- Transport agreement (G14): an endpoint or job must name its identity's transport.
CREATE TRIGGER destinations_transport_matches_identity BEFORE INSERT ON destinations
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'endpoint transport mismatch'); END;
CREATE TRIGGER contact_points_transport_matches_identity BEFORE INSERT ON contact_points
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'endpoint transport mismatch'); END;
CREATE TRIGGER delivery_jobs_transport_matches_identity BEFORE INSERT ON delivery_jobs
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'job transport mismatch'); END;
-- An origin's endpoint must resolve to its job's (transport, identity) (G14).
CREATE TRIGGER job_origins_endpoint_matches_job BEFORE INSERT ON job_origins
  WHEN NOT EXISTS (SELECT 1 FROM delivery_jobs j WHERE j.id = NEW.job_id AND (
    EXISTS (SELECT 1 FROM destinations d WHERE d.ref = NEW.endpoint_ref AND d.identity_id = j.identity_id AND d.transport = j.transport)
    OR EXISTS (SELECT 1 FROM contact_points c WHERE c.ref = NEW.endpoint_ref AND c.identity_id = j.identity_id AND c.transport = j.transport)))
  BEGIN SELECT RAISE(ABORT, 'origin endpoint does not match its job'); END;
-- The current generation belongs to its campaign and is active (G16).
CREATE TRIGGER campaigns_insert_has_no_generation BEFORE INSERT ON campaigns
  WHEN NEW.current_generation_id IS NOT NULL BEGIN SELECT RAISE(ABORT, 'generation belongs to another campaign'); END;
CREATE TRIGGER campaigns_generation_owned BEFORE UPDATE OF current_generation_id ON campaigns
  WHEN NEW.current_generation_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM generations g
    WHERE g.id = NEW.current_generation_id AND g.campaign_id = NEW.id AND g.status = 'active')
  BEGIN SELECT RAISE(ABORT, 'generation belongs to another campaign'); END;
-- Immutability (R11, §7.1).
CREATE TRIGGER destinations_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, location_id, ref
  ON destinations BEGIN SELECT RAISE(ABORT, 'destination identity is immutable'); END;
CREATE TRIGGER contact_points_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, recipient_id, ref
  ON contact_points BEGIN SELECT RAISE(ABORT, 'contact point identity is immutable'); END;
CREATE TRIGGER delivery_identities_immutable BEFORE UPDATE ON delivery_identities
  BEGIN SELECT RAISE(ABORT, 'delivery identity is immutable'); END;
CREATE TRIGGER generations_frozen BEFORE UPDATE OF ref, campaign_id, created_at, send_at, content, snapshot_digest
  ON generations BEGIN SELECT RAISE(ABORT, 'generation is frozen'); END;
CREATE TRIGGER generations_status_one_way BEFORE UPDATE OF status ON generations
  WHEN NOT (OLD.status = 'active' AND NEW.status = 'discarded')
  BEGIN SELECT RAISE(ABORT, 'generation is frozen'); END;
CREATE TRIGGER jobs_binding_frozen BEFORE UPDATE OF ref, generation_id, transport, identity_id,
  idempotency_key, payload, payload_digest, skip_reason ON delivery_jobs
  BEGIN SELECT RAISE(ABORT, 'job binding is frozen'); END;
CREATE TRIGGER job_origins_frozen_u BEFORE UPDATE ON job_origins BEGIN SELECT RAISE(ABORT, 'origins are frozen'); END;
CREATE TRIGGER job_origins_frozen_d BEFORE DELETE ON job_origins BEGIN SELECT RAISE(ABORT, 'origins are frozen'); END;
CREATE TRIGGER attempts_binding_frozen BEFORE UPDATE OF ref, job_id, attempt_no, started_at ON delivery_attempts
  BEGIN SELECT RAISE(ABORT, 'attempt binding is frozen'); END;
CREATE TRIGGER attempts_provider_ref_set_once BEFORE UPDATE OF provider_message_ref ON delivery_attempts
  WHEN OLD.provider_message_ref IS NOT NULL BEGIN SELECT RAISE(ABORT, 'provider reference is frozen'); END;
CREATE TRIGGER provider_events_only_pending_resolves BEFORE UPDATE ON provider_events
  WHEN OLD.disposition <> 'pending_match' OR NEW.transport IS NOT OLD.transport
    OR NEW.provider_event_ref IS NOT OLD.provider_event_ref OR NEW.provider_message_ref IS NOT OLD.provider_message_ref
    OR NEW.reported_status IS NOT OLD.reported_status OR NEW.received_at IS NOT OLD.received_at
  BEGIN SELECT RAISE(ABORT, 'provider event is frozen'); END;
CREATE TRIGGER provider_events_no_delete BEFORE DELETE ON provider_events
  BEGIN SELECT RAISE(ABORT, 'provider event is frozen'); END;
-- Endpoints are disabled, never deleted (§2): job origins name them by ref, not by FK.
CREATE TRIGGER destinations_never_deleted BEFORE DELETE ON destinations
  BEGIN SELECT RAISE(ABORT, 'endpoints are disabled, never deleted'); END;
CREATE TRIGGER contact_points_never_deleted BEFORE DELETE ON contact_points
  BEGIN SELECT RAISE(ABORT, 'endpoints are disabled, never deleted'); END;
CREATE TRIGGER campaign_events_append_only_u BEFORE UPDATE ON campaign_events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER campaign_events_append_only_d BEFORE DELETE ON campaign_events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
"""


def _statements(sql: str) -> tuple[str, ...]:
    """One statement per entry: whole-line comments dropped, then split on ";\n" (trigger
    bodies are single-line, so their inner ";" is never followed by a newline)."""
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return tuple(part.strip() for part in "\n".join(lines).split(";\n") if part.strip())


SCHEMA_V1: tuple[str, ...] = _statements(_SCHEMA_V1_SQL)

# comms v0.3: assembled across Part A's tasks before the single merge (plan Global Constraints).
_SCHEMA_V2_SQL = """
CREATE TABLE key_slots (purpose TEXT NOT NULL, version INTEGER NOT NULL CHECK (version >= 1), key_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('ACTIVE','TRUSTED_RETIRED','VERIFICATION_ONLY','REVOKED','RETIRED','ORPHAN','DESTROYED')),
  created_at TEXT NOT NULL, retired_at TEXT, PRIMARY KEY (purpose, version));
CREATE UNIQUE INDEX key_slots_one_active ON key_slots (purpose) WHERE state = 'ACTIVE';
CREATE TRIGGER key_slots_binding_immutable BEFORE UPDATE OF purpose, version, key_id, created_at ON key_slots
  BEGIN SELECT RAISE(ABORT, 'key slot binding is immutable'); END;
CREATE TABLE verification_keys (key_id TEXT PRIMARY KEY, purpose TEXT NOT NULL, algorithm TEXT NOT NULL,
  public_key BLOB NOT NULL, activated_at TEXT NOT NULL, retired_at TEXT,
  trust_state TEXT NOT NULL CHECK (trust_state IN ('ACTIVE','TRUSTED_RETIRED','VERIFICATION_ONLY','REVOKED')));
CREATE TRIGGER verification_keys_public_immutable BEFORE UPDATE OF key_id, purpose, algorithm, public_key
  ON verification_keys BEGIN SELECT RAISE(ABORT, 'verification key is immutable'); END;
CREATE TABLE audit_events (event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, subject_ref TEXT,
  subject_digest TEXT, payload TEXT NOT NULL,
  chain_epoch INTEGER NOT NULL CHECK (chain_epoch >= 1), chain_seq INTEGER NOT NULL CHECK (chain_seq >= 1),
  prev_event_mac TEXT NOT NULL, event_mac TEXT NOT NULL, UNIQUE (chain_epoch, chain_seq));
CREATE TABLE audit_checkpoints (checkpoint_ref TEXT PRIMARY KEY, chain_epoch INTEGER NOT NULL,
  chain_seq INTEGER NOT NULL, last_event_id TEXT NOT NULL, last_event_mac TEXT NOT NULL, reason TEXT NOT NULL,
  created_at TEXT NOT NULL, signing_key_id TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TRIGGER audit_events_append_only_u BEFORE UPDATE ON audit_events
  BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER audit_events_append_only_d BEFORE DELETE ON audit_events
  BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER audit_checkpoints_append_only_u BEFORE UPDATE ON audit_checkpoints
  BEGIN SELECT RAISE(ABORT, 'checkpoints are append-only'); END;
CREATE TRIGGER audit_checkpoints_append_only_d BEFORE DELETE ON audit_checkpoints
  BEGIN SELECT RAISE(ABORT, 'checkpoints are append-only'); END;
CREATE TABLE audit_integrity (id INTEGER PRIMARY KEY CHECK (id = 1),
  state TEXT NOT NULL CHECK (state IN ('ok','degraded')), reason TEXT, since TEXT);
INSERT INTO audit_integrity (id, state) VALUES (1, 'ok');
CREATE TABLE audit_lineage (cutover_ref TEXT PRIMARY KEY, legacy_chain_domain TEXT NOT NULL,
  legacy_final_epoch INTEGER NOT NULL, legacy_final_head TEXT NOT NULL, legacy_checkpoint_digest TEXT NOT NULL,
  legacy_checkpoint_key_id TEXT NOT NULL, comms_chain_domain TEXT NOT NULL, comms_genesis_digest TEXT NOT NULL,
  comms_first_epoch INTEGER NOT NULL, lineage_digest TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TRIGGER audit_lineage_immutable_u BEFORE UPDATE ON audit_lineage
  BEGIN SELECT RAISE(ABORT, 'lineage is immutable'); END;
CREATE TRIGGER audit_lineage_immutable_d BEFORE DELETE ON audit_lineage
  BEGIN SELECT RAISE(ABORT, 'lineage is immutable'); END;
CREATE TABLE cutover_state (id INTEGER PRIMARY KEY CHECK (id = 1), cutover_ref TEXT,
  phase TEXT NOT NULL CHECK (phase IN ('NONE','CUTOVER_ENTERED','LEGACY_DRAINED','LEGACY_VERIFIED','LEGACY_SEALED',
    'LEGACY_ANCHORED','COMMS_GENESIS','COMMS_ANCHORED','LEGACY_CLIENT_AUTH_REVOKED','COMPLETE')),
  legacy_checkpoint_digest TEXT, updated_at TEXT NOT NULL);
INSERT INTO cutover_state (id, phase, updated_at) VALUES (1, 'NONE', '1970-01-01T00:00:00.000000Z');
CREATE TABLE cutover_transitions (from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, PRIMARY KEY (from_phase, to_phase));
INSERT INTO cutover_transitions VALUES ('NONE','CUTOVER_ENTERED'), ('CUTOVER_ENTERED','LEGACY_DRAINED'),
  ('LEGACY_DRAINED','LEGACY_VERIFIED'), ('LEGACY_VERIFIED','LEGACY_SEALED'), ('LEGACY_SEALED','LEGACY_ANCHORED'),
  ('LEGACY_ANCHORED','COMMS_GENESIS'), ('COMMS_GENESIS','COMMS_ANCHORED'),
  ('COMMS_ANCHORED','LEGACY_CLIENT_AUTH_REVOKED'), ('LEGACY_CLIENT_AUTH_REVOKED','COMPLETE');
CREATE TRIGGER cutover_transitions_fixed_u BEFORE UPDATE ON cutover_transitions
  BEGIN SELECT RAISE(ABORT, 'cutover transitions are fixed'); END;
CREATE TRIGGER cutover_transitions_fixed_d BEFORE DELETE ON cutover_transitions
  BEGIN SELECT RAISE(ABORT, 'cutover transitions are fixed'); END;
CREATE TRIGGER cutover_exact_next_state BEFORE UPDATE OF phase ON cutover_state
  WHEN NEW.phase IS NOT OLD.phase AND NOT EXISTS (SELECT 1 FROM cutover_transitions
       WHERE from_phase = OLD.phase AND to_phase = NEW.phase)
  BEGIN SELECT RAISE(ABORT, 'cutover moves only to its exact next state'); END;
CREATE TRIGGER cutover_fields_immutable BEFORE UPDATE OF cutover_ref, legacy_checkpoint_digest ON cutover_state
  WHEN (OLD.cutover_ref IS NOT NULL AND NEW.cutover_ref IS NOT OLD.cutover_ref)
    OR (OLD.legacy_checkpoint_digest IS NOT NULL AND NEW.legacy_checkpoint_digest IS NOT OLD.legacy_checkpoint_digest)
  BEGIN SELECT RAISE(ABORT, 'cutover identity is immutable'); END;
ALTER TABLE campaign_events ADD COLUMN event_digest TEXT;
"""
SCHEMA_V2: tuple[str, ...] = _statements(_SCHEMA_V2_SQL)

# comms v0.3 A12 (Task B8): the keyed campaign commitment on each generation. NULL only for
# a generation frozen without the audit writer (the 5b-4 unit harness), which never
# reaches the chain.
_SCHEMA_V3_SQL = """
ALTER TABLE generations ADD COLUMN campaign_commitment TEXT
  CHECK (campaign_commitment IS NULL OR length(campaign_commitment) = 64);
ALTER TABLE generations ADD COLUMN campaign_commit_key_id TEXT;
CREATE TRIGGER generations_commitment_immutable BEFORE UPDATE OF campaign_commitment, campaign_commit_key_id
  ON generations BEGIN SELECT RAISE(ABORT, 'a campaign commitment is immutable'); END;
CREATE TRIGGER verification_keys_trust_one_way BEFORE UPDATE OF trust_state ON verification_keys
  WHEN (CASE NEW.trust_state WHEN 'ACTIVE' THEN 0 WHEN 'TRUSTED_RETIRED' THEN 1
          WHEN 'VERIFICATION_ONLY' THEN 2 ELSE 3 END)
     < (CASE OLD.trust_state WHEN 'ACTIVE' THEN 0 WHEN 'TRUSTED_RETIRED' THEN 1
          WHEN 'VERIFICATION_ONLY' THEN 2 ELSE 3 END)
  BEGIN SELECT RAISE(ABORT, 'signer trust is one-way'); END;
CREATE TABLE maintenance_flags (name TEXT PRIMARY KEY, value INTEGER NOT NULL CHECK (value IN (0, 1)));
INSERT INTO maintenance_flags (name, value) VALUES ('truncating', 0);
DROP TRIGGER audit_events_append_only_d;
CREATE TRIGGER audit_events_delete_only_behind_root BEFORE DELETE ON audit_events
  WHEN (SELECT value FROM maintenance_flags WHERE name = 'truncating') IS NOT 1
  BEGIN SELECT RAISE(ABORT, 'audit is append-only: deletes only by truncation behind a root'); END;
-- A15 (Task B20): directory identities of endpoints disabled past the retention window are
-- replaced by 'redacted:<delivery_identities.id>' (unique per row); nothing else may change.
ALTER TABLE destinations ADD COLUMN disabled_at TEXT;
ALTER TABLE contact_points ADD COLUMN disabled_at TEXT;
DROP TRIGGER delivery_identities_immutable;
CREATE TRIGGER delivery_identities_immutable BEFORE UPDATE ON delivery_identities
  WHEN NOT (NEW.id IS OLD.id AND NEW.transport IS OLD.transport AND NEW.identity = 'redacted:' || OLD.id
    AND OLD.identity NOT LIKE 'redacted:%')
  BEGIN SELECT RAISE(ABORT, 'delivery identity is immutable'); END;
DROP TRIGGER destinations_identity_immutable;
CREATE TRIGGER destinations_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, location_id, ref
  ON destinations WHEN NOT (NEW.transport IS OLD.transport AND NEW.identity_id IS OLD.identity_id
    AND NEW.location_id IS OLD.location_id AND NEW.ref IS OLD.ref
    AND NEW.platform_identity = 'redacted:' || OLD.identity_id)
  BEGIN SELECT RAISE(ABORT, 'destination identity is immutable'); END;
DROP TRIGGER contact_points_identity_immutable;
CREATE TRIGGER contact_points_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, recipient_id, ref
  ON contact_points WHEN NOT (NEW.transport IS OLD.transport AND NEW.identity_id IS OLD.identity_id
    AND NEW.recipient_id IS OLD.recipient_id AND NEW.ref IS OLD.ref
    AND NEW.platform_identity = 'redacted:' || OLD.identity_id)
  BEGIN SELECT RAISE(ABORT, 'contact point identity is immutable'); END;
-- A15 (Task B19): campaign-body redaction. The only edit a frozen generation or job ever
-- accepts is its body going (content -> '{}', payload -> NULL) with redacted_at set once;
-- every digest stays. delivery_jobs is rebuilt to relax its payload CHECKs for that case.
ALTER TABLE generations ADD COLUMN redacted_at TEXT;
DROP TRIGGER generations_frozen;
CREATE TRIGGER generations_frozen BEFORE UPDATE OF ref, campaign_id, created_at, send_at, content,
  snapshot_digest, redacted_at ON generations
  WHEN NOT (NEW.ref IS OLD.ref AND NEW.campaign_id IS OLD.campaign_id AND NEW.created_at IS OLD.created_at
    AND NEW.send_at IS OLD.send_at AND NEW.snapshot_digest IS OLD.snapshot_digest
    AND OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.content = '{}')
  BEGIN SELECT RAISE(ABORT, 'generation is frozen'); END;
CREATE TABLE delivery_jobs_v3 (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')),
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) = 64),
  payload BLOB, payload_digest TEXT, skip_reason TEXT,
  state TEXT NOT NULL CHECK (state IN ('PENDING','IN_FLIGHT','ACCEPTED','DELIVERED','FAILED_TRANSIENT',
    'FAILED_PERMANENT','OUTCOME_UNKNOWN','CANCELLED','SKIPPED_PLATFORM_POLICY','SKIPPED_REVALIDATION')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  redacted_at TEXT,
  UNIQUE (generation_id, transport, identity_id),
  CHECK ((payload IS NULL) = (payload_digest IS NULL) OR redacted_at IS NOT NULL),
  CHECK ((payload IS NULL) = (skip_reason IS NOT NULL) OR redacted_at IS NOT NULL),
  CHECK ((state = 'SKIPPED_PLATFORM_POLICY') = (payload IS NULL) OR redacted_at IS NOT NULL),
  CHECK (redacted_at IS NULL OR payload IS NULL),
  CHECK (payload_digest IS NULL OR (length(payload_digest) = 64 AND payload_digest NOT GLOB '*[^0-9a-f]*')));
INSERT INTO delivery_jobs_v3 (id, ref, generation_id, transport, identity_id, idempotency_key, payload,
  payload_digest, skip_reason, state, attempt_count)
  SELECT id, ref, generation_id, transport, identity_id, idempotency_key, payload, payload_digest,
    skip_reason, state, attempt_count FROM delivery_jobs;
DROP TRIGGER job_origins_endpoint_matches_job;
DROP TABLE delivery_jobs;
ALTER TABLE delivery_jobs_v3 RENAME TO delivery_jobs;
CREATE TRIGGER job_origins_endpoint_matches_job BEFORE INSERT ON job_origins
  WHEN NOT EXISTS (SELECT 1 FROM delivery_jobs j WHERE j.id = NEW.job_id AND (
    EXISTS (SELECT 1 FROM destinations d WHERE d.ref = NEW.endpoint_ref AND d.identity_id = j.identity_id AND d.transport = j.transport)
    OR EXISTS (SELECT 1 FROM contact_points c WHERE c.ref = NEW.endpoint_ref AND c.identity_id = j.identity_id AND c.transport = j.transport)))
  BEGIN SELECT RAISE(ABORT, 'origin endpoint does not match its job'); END;
CREATE TRIGGER delivery_jobs_transport_matches_identity BEFORE INSERT ON delivery_jobs
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'job transport mismatch'); END;
CREATE TRIGGER jobs_binding_frozen BEFORE UPDATE OF ref, generation_id, transport, identity_id,
  idempotency_key, payload, payload_digest, skip_reason, redacted_at ON delivery_jobs
  WHEN NOT (NEW.ref IS OLD.ref AND NEW.generation_id IS OLD.generation_id AND NEW.transport IS OLD.transport
    AND NEW.identity_id IS OLD.identity_id AND NEW.idempotency_key IS OLD.idempotency_key
    AND NEW.payload_digest IS OLD.payload_digest AND NEW.skip_reason IS OLD.skip_reason
    AND OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.payload IS NULL)
  BEGIN SELECT RAISE(ABORT, 'job binding is frozen'); END;
"""
SCHEMA_V3: tuple[str, ...] = _statements(_SCHEMA_V3_SQL)

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, SCHEMA_V1),
    Migration(2, SCHEMA_V2),
    Migration(3, SCHEMA_V3, rebuild=True),
)


def migrate(conn: Any, migrations: tuple[Migration, ...] = MIGRATIONS) -> int:
    """Apply every migration not yet recorded; return the resulting version."""
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("migrations must be numbered 1..n in order")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    done = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    prior_fk = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    for migration in migrations:
        if migration.version in done:
            continue
        if migration.rebuild:
            conn.execute("PRAGMA foreign_keys = OFF")  # outside the transaction, or it is ignored
        try:
            with write_tx(conn):
                for statement in migration.statements:
                    conn.execute(statement)
                if migration.rebuild and conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("foreign key check failed after rebuild")
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (migration.version,)
                )
        finally:
            if migration.rebuild:
                conn.execute(f"PRAGMA foreign_keys = {'ON' if prior_fk else 'OFF'}")
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return int(row[0] or 0)
