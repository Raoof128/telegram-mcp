-- Phase 5 control schema: templates (#17/#36), persistent scheduler (#45), status
-- reconciliation candidates (#59). generation_mode CHECK enforces "no autonomous LLM"
-- at the schema level too (#47).
CREATE TABLE templates (
    template_id TEXT PRIMARY KEY,
    meta_template_id TEXT,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL,
    definition_version INTEGER NOT NULL DEFAULT 1,
    schema TEXT,
    synced_at INTEGER
);

CREATE TABLE scheduled_jobs (
    job_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    account_id TEXT,
    timezone TEXT,
    schedule TEXT,
    generation_mode TEXT NOT NULL DEFAULT 'static' CHECK (generation_mode IN ('static','template')),
    conditions TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    max_lateness_ms INTEGER,
    last_run_ms INTEGER,
    next_run_ms INTEGER,
    created_at_ms INTEGER
);

CREATE TABLE job_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES scheduled_jobs(job_id),
    fired_at_ms INTEGER,
    outcome TEXT,
    draft_id TEXT
);

CREATE TABLE reconciliation_candidates (
    id TEXT PRIMARY KEY,
    wamid TEXT,
    recipient_id TEXT,
    provider_ts_ms INTEGER,
    status TEXT,
    candidate_attempt_id TEXT,
    state TEXT NOT NULL DEFAULT 'POSSIBLE_MATCH'
        CHECK (state IN ('POSSIBLE_MATCH','RESOLVED','DISMISSED')),
    created_at_ms INTEGER,
    resolved_at_ms INTEGER,
    resolution TEXT
);
