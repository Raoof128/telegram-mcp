"""Typed audit events: each kind has an exact payload schema (comms v0.3 A7, G5).

Unknown kinds and unknown keys fail; every value is checked by a finite-domain
validator. Later tasks register their kinds here, never elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core import domains
from comms.core.campaigns.events import EVENT_TYPES
from comms.core.keys.purposes import PURPOSES
from comms.core.validators import Validator, count, digest, key_id, one_of

__all__ = ["AUDIT_EVENT_SPECS", "SUBJECT_KINDS", "validate_audit_event"]

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
    "admin.key_rotation": {
        "purpose": one_of(set(PURPOSES)),
        "old_version": count(0),
        "new_version": count(1),
        "key_id": key_id,
    },
}
# The ref kind each event's subject must carry (None: the event has no subject).
SUBJECT_KINDS: dict[str, str | None] = {
    "campaign_event": "event",
    "system.test_marker": None,
    "system.audit_cutover": "cutover",
    "system.legacy_client_auth_revoked": "cutover",
    "admin.key_rotation": None,
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
