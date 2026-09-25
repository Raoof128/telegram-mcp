"""Typed audit events: each kind has an exact payload schema (comms v0.3 A7, G5).

Unknown kinds and unknown keys fail; every value is checked by a finite-domain
validator. Later tasks register their kinds here, never elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from comms.core import domains
from comms.core.campaigns.events import EVENT_TYPES
from comms.core.keys.purposes import PURPOSES
from comms.core.providers.capability import Capability
from comms.core.validators import (
    Validator,
    count,
    digest,
    key_id,
    nullable,
    one_of,
    opaque_ref,
    ref,
)

__all__ = ["AUDIT_EVENT_SPECS", "RETENTION_PHASES", "SUBJECT_KINDS", "validate_audit_event"]

# The retention runner's phases, in order (design §B.8); the purge event counts each.
RETENTION_PHASES = (
    "legacy_exposure",
    "legacy_chain",
    "legacy_receipts",
    "legacy_message_refs",
    "comms_chain",
    "campaign_bodies",
    "inbound_bodies",  # Part C exit follow-up (A15)
    "identities",
    "public_keys",
    "secrets",
)
_CREDENTIALS = {name for name, p in PURPOSES.items() if p.rotation == "staged"}
_ACTORS = {"telegram_bot", "telegram_user", "whatsapp_cloud", "whatsapp_webhooks"}


def _matches(pattern: str) -> Validator:
    compiled = re.compile(pattern)
    return lambda value: isinstance(value, str) and compiled.fullmatch(value) is not None


_TOOL = _matches(r"comms_[a-z0-9_]{1,64}")
_CODE = _matches(r"[A-Z][A-Z_]{0,39}")

AUDIT_EVENT_SPECS: dict[str, Mapping[str, Validator]] = {
    "campaign_event": {"event_type": one_of(EVENT_TYPES)},
    "system.test_marker": {"count": count(0)},
    "system.audit_cutover": {
        "legacy_checkpoint_digest": digest,
        "legacy_final_epoch": count(1),
        "legacy_final_head": digest,
        "legacy_checkpoint_key_id": key_id,
        "comms_chain_domain": one_of({domains.AUDIT_CHAIN.rstrip(b"\0").decode()}),
        "comms_epoch": count(1),
        "comms_audit_key_id": key_id,
    },
    "system.legacy_client_auth_revoked": {"revoked_count": count(0), "security_epoch": count(1)},
    "campaign_event_committed": {
        "event_type": one_of({"campaign.send_started", "campaign.scheduled"}),
        "commitment": digest,
        "commit_key_id": key_id,
        "generation": ref("generation"),
    },
    "admin.signer_trust": {
        "key_id": key_id,
        "from_state": one_of({"ACTIVE", "TRUSTED_RETIRED", "VERIFICATION_ONLY"}),
        "to_state": one_of({"TRUSTED_RETIRED", "VERIFICATION_ONLY", "REVOKED"}),
    },
    "admin.credential_rotation": {
        "purpose": one_of(_CREDENTIALS),
        "old_version": count(0),
        "new_version": count(1),
    },
    "admin.credential_rotation_rolled_back": {
        "purpose": one_of(_CREDENTIALS),
        "restored_version": count(0),  # 0: a first activation had nothing to restore
        "orphaned_version": count(1),
    },
    "admin.credential_revoked": {"purpose": one_of(_CREDENTIALS), "version": count(1)},
    "admin.session_revoke": {
        "phase": one_of({"started", "finished"}),
        "outcome": one_of({"pending", "confirmed", "failed", "unknown", "aborted"}),
        "security_epoch": count(1),
    },
    "maintenance.retention_purge": {
        **{phase: count(0) for phase in RETENTION_PHASES},
        "comms_root": nullable(ref("audit_checkpoint")),
        "legacy_root": nullable(opaque_ref),
    },
    "admin.audit_repair": {
        "anchor_epoch": count(1),
        "anchor_seq": count(1),
        "head_epoch": count(1),
        "head_seq": count(1),
    },
    "admin.backup_import": {"binding": digest, "chain_key_id": key_id},
    "admin.backup_adopt": {"old_binding": digest, "new_binding": digest},
    "admin.backup_export": {
        "binding": digest,
        "ciphertext_sha256": digest,
        "signer_key_id": key_id,
    },
    "admin.key_rotation": {
        "purpose": one_of(set(PURPOSES)),
        "old_version": count(0),
        "new_version": count(1),
        "key_id": key_id,
    },
    # D4 (A28, A41): every write request, its saga steps and its outcome.
    "admin.mutation_started": {
        "tool": _TOOL,
        "scope": one_of({"local", "provider"}),
        "actor": nullable(one_of(_ACTORS)),
    },
    "admin.mutation_step": {
        "step_no": count(1),
        "capability": one_of({c.value for c in Capability}),
        "state": one_of({"SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"}),
        "provider_code": nullable(_CODE),
    },
    "admin.mutation_finished": {
        "state": one_of({"SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"}),
        "provider_code": nullable(_CODE),
    },
}
# The ref kind each event's subject must carry (None: the event has no subject).
SUBJECT_KINDS: dict[str, str | None] = {
    "campaign_event": "event",
    "system.test_marker": None,
    "system.audit_cutover": "cutover",
    "system.legacy_client_auth_revoked": "cutover",
    "campaign_event_committed": "event",
    "admin.signer_trust": None,
    "admin.credential_rotation": None,
    "admin.credential_rotation_rolled_back": None,
    "admin.credential_revoked": None,
    "admin.session_revoke": None,
    "maintenance.retention_purge": None,
    "admin.audit_repair": None,
    "admin.backup_import": None,
    "admin.backup_adopt": None,
    "admin.backup_export": None,
    "admin.key_rotation": None,
    "admin.mutation_started": "operation",
    "admin.mutation_step": "operation",
    "admin.mutation_finished": "operation",
}


def validate_audit_event(
    kind: str, subject_ref: str | None, subject_digest: str | None, payload: Mapping[str, Any]
) -> None:
    spec = AUDIT_EVENT_SPECS.get(kind)
    if spec is None:
        raise ValueError("unknown audit event kind")
    if set(payload) != set(spec):
        raise ValueError("audit payload keys refused")
    for key, value in payload.items():
        if not spec[key](value):
            raise ValueError("audit payload field refused")
    subject_kind = SUBJECT_KINDS[kind]
    if subject_kind is None:
        if subject_ref is not None or subject_digest is not None:
            raise ValueError("audit event takes no subject")
        return
    from comms.core import refs

    try:
        refs.check(subject_ref, subject_kind)
    except ValueError:
        raise ValueError("audit subject refused") from None
    if subject_digest is not None and not digest(subject_digest):
        raise ValueError("audit subject digest refused")
