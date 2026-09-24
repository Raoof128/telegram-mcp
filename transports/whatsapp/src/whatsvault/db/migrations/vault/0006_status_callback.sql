-- Phase 5: carry biz_opaque_callback_data on the normalised status evidence (ledger #60,
-- PHASE-0-CONTINGENT on V8). Set at insert; not in the status-evidence freeze list.
ALTER TABLE message_status_events ADD COLUMN biz_opaque_callback_data TEXT;
