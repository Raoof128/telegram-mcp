-- Phase 4: two-key device identity (#5) + distinct action target (#10).
ALTER TABLE approval_devices ADD COLUMN agreement_public_key BLOB;
ALTER TABLE approval_devices ADD COLUMN agreement_key_algorithm TEXT;
ALTER TABLE drafts ADD COLUMN target_message_wamid TEXT;

-- Recreate the draft-freeze trigger to also freeze the new signed field (SR-1).
DROP TRIGGER trg_draft_freeze;
CREATE TRIGGER trg_draft_freeze
BEFORE UPDATE OF body_bytes, body_sha256, recipient_wa_id, recipient_id, account_id,
                 phone_number_id, nonce, expires_at_ms, kind, template_id,
                 template_params_sha256, attachments_digest, reply_to_wamid, target_message_wamid
ON drafts
WHEN OLD.state <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'draft core fields freeze once state leaves DRAFT'); END;
