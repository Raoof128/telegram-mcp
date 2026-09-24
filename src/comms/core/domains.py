"""Every wire domain the campaign core hashes under (comms 5b-4, G30).

The only home of core ``comms-*`` constants; ``tests/security/test_comms_wire_frozen.py``
pins the multiset and refuses a core domain defined anywhere else.
"""

from __future__ import annotations

__all__ = [
    "ADMIN_OP",
    "AUDIT_CHAIN",
    "AUDIT_CHECKPOINT",
    "AUDIT_GENESIS",
    "CURSOR",
    "IDEMPOTENCY",
    "MTPROTO_RANDOM_ID",
    "RECIPIENTS",
    "REQUEST_DIGEST",
    "SNAPSHOT",
    "TARGET",
]

IDEMPOTENCY = b"comms-delivery-idem/v1\0"  # §7.1
SNAPSHOT = b"comms-campaign-snapshot/v1\0"  # §5.4
TARGET = b"comms-campaign-target/v1\0"  # §9, S5
RECIPIENTS = b"comms-campaign-recipients/v1\0"  # §9, S5
AUDIT_CHAIN = b"comms-audit-chain/v1\0"  # comms v0.3 A7
AUDIT_GENESIS = b"comms-audit-genesis/v1\0"  # comms v0.3 A7
AUDIT_CHECKPOINT = b"comms-audit-checkpoint/v1\0"  # comms v0.3 A7
AUDIT_HEAD_ANCHOR = b"comms/audit-head-anchor/v1\0"  # comms v0.3 A8
CAMPAIGN_EVENT = b"comms-campaign-event/v1\0"  # comms v0.3 A16/G4
CAMPAIGN_COMMIT = b"comms-campaign-commit/v1\0"  # comms v0.3 A12, D2
BACKUP_SIGNATURE = b"comms-backup-signature/v1\0"  # comms v0.3 B24
MTPROTO_RANDOM_ID = b"comms-mtproto-random-id/v1\0"  # comms v0.3 A20 (Task C15)
REQUEST_DIGEST = b"comms-request-digest/v1\0"  # comms v0.3 A28 (Task D4)
ADMIN_OP = b"comms-admin-op/v1\0"  # comms v0.3 A28 (Task D4)
CURSOR = b"comms-cursor/v1\0"  # comms v0.3 A30 (Task D9)
BACKUP_BINDING = b"comms-backup-binding/v1\0"  # comms v0.3 B25
BACKUP_SCHEMA = b"comms-backup/v1\0"  # comms v0.3 B25: the payload's schema name
