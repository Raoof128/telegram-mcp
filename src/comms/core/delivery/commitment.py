"""The keyed campaign commitment (comms v0.3 A12, design §B.5, D2).

A campaign snapshot reaches the audit chain only as ``HMAC(campaign-commit-key,
"comms-campaign-commit/v1\\0" ‖ generation_ref ‖ snapshot_digest)``, never as the snapshot
digest itself. The commitment and its key ID are stored on the generation row, and the
freeze event's chain event carries both. Verification recomputes the HMAC from the stored
``snapshot_digest`` under the key the row names, so it survives redaction of the bodies
and rotation of the key (old keys are kept while their commitments are retained).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from comms.core import domains
from comms.core.audit.writer import AuditWriter
from comms.core.keys.slots import KeySlotError, KeySlotStore, load_active, load_version

__all__ = ["CommitContext", "campaign_commitment", "commit_context", "verify_commitment"]

_PURPOSE = "campaign-commit-key"


def campaign_commitment(key: bytes, generation_ref: str, snapshot_digest: str) -> str:
    return hmac.new(
        key,
        domains.CAMPAIGN_COMMIT + generation_ref.encode() + bytes.fromhex(snapshot_digest),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class CommitContext:
    """What an audited freeze needs: the writer, and the active commit key and its ID."""

    writer: AuditWriter
    key: bytes
    key_id: str

    def __repr__(self) -> str:
        return f"CommitContext(key_id={self.key_id!r})"


def commit_context(writer: AuditWriter, store: KeySlotStore) -> CommitContext:
    key, key_id = load_active(writer.conn, store, _PURPOSE)
    return CommitContext(writer, key, key_id)


def verify_commitment(conn: Any, store: KeySlotStore, generation_ref: str) -> bool:
    row = conn.execute(
        "SELECT snapshot_digest, campaign_commitment, campaign_commit_key_id FROM generations WHERE ref = ?",
        (generation_ref,),
    ).fetchone()
    if row is None or row[1] is None or row[2] is None:
        return False
    version = conn.execute(
        "SELECT version FROM key_slots WHERE purpose = ? AND key_id = ?", (_PURPOSE, row[2])
    ).fetchone()
    if version is None:
        return False
    try:
        key = load_version(conn, store, _PURPOSE, int(version[0]))
    except KeySlotError:
        return False
    return hmac.compare_digest(campaign_commitment(key, generation_ref, row[0]), row[1])
