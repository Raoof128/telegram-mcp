"""The retention runner (comms v0.3 A15, design §B.8): foreign-key order, fail closed.

The legacy phases (Task B17), in the order the legacy foreign keys force (N8):

1. exposure rows older than ``exposure_ledger_days``;
2. legacy chain truncation strictly before the latest verified checkpoint at or before
   ``audit_events_days`` (Task B3) — checkpoints, the final ``V0_3_CUTOVER`` seal and its
   marker row are never deleted;
3. receipts older than ``receipt_days`` once nothing references them (their exposure and
   audit rows are gone, so the foreign keys make this mechanical);
4. ``message_refs`` older than ``message_ref_days`` that no live cursor names.

Then the comms chain prefix (Task B18): events strictly before the latest verified comms
checkpoint at or before ``audit_events_days``; then campaign bodies (Task B19) of COMPLETE
or CANCELLED campaigns frozen before ``campaign_body_days``.

Core never reads the Telegram schema: the transport supplies a ``LegacyRetention``.
Retention refuses while the audit integrity latch is set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.audit.chain import COMMS, append_guard
from comms.core.audit.integrity import require_not_degraded
from comms.core.audit.retention_root import choose_root
from comms.core.audit.verify_all import LegacyVerify
from comms.core.audit.writer import AuditWriter
from comms.core.keys.slots import registry_public_for
from comms.core.maintenance.redaction import redact_campaign_bodies
from comms.core.storage.db import write_tx

__all__ = ["LegacyRetention", "RetentionPolicy", "RetentionReport", "run_retention"]


@dataclass(frozen=True)
class RetentionPolicy:
    exposure_ledger_days: int
    receipt_days: int
    message_ref_days: int
    audit_events_days: int
    campaign_body_days: int
    identity_retention_days: int


@dataclass(frozen=True)
class RetentionReport:
    phases: Mapping[str, int]
    roots: Mapping[str, str | None]
    outcome: str


class LegacyRetention(Protocol):
    conn: Any
    verify: LegacyVerify

    def purge_exposure(self, cutoff: datetime) -> int: ...
    def truncate_chain_before(self, root: Mapping[str, Any]) -> int: ...
    def purge_receipts(self, cutoff: datetime) -> int: ...
    def purge_message_refs(self, cutoff: datetime, *, now: datetime) -> int: ...


def run_retention(
    comms_conn: Any,
    legacy: LegacyRetention,
    policy: RetentionPolicy,
    writer: AuditWriter,
    *,
    now: datetime,
) -> RetentionReport:
    require_not_degraded(comms_conn)
    if writer.conn is not comms_conn:
        raise ValueError("the audit writer must own the comms connection")
    now = timeutil.utc(now)

    def before(days: int) -> datetime:
        return now - timedelta(days=int(days))

    phases: dict[str, int] = {
        "legacy_exposure": legacy.purge_exposure(before(policy.exposure_ledger_days))
    }
    root = choose_root(
        legacy.conn,
        legacy.verify.profile,
        cutoff=timeutil.iso(before(policy.audit_events_days)),
        public_keys=legacy.verify.public_for,
    )
    phases["legacy_chain"] = legacy.truncate_chain_before(root) if root is not None else 0
    phases["legacy_receipts"] = legacy.purge_receipts(before(policy.receipt_days))
    phases["legacy_message_refs"] = legacy.purge_message_refs(
        before(policy.message_ref_days), now=now
    )
    comms_root, phases["comms_chain"] = _truncate_comms(
        comms_conn, before(policy.audit_events_days)
    )
    with write_tx(comms_conn):
        phases["campaign_bodies"] = redact_campaign_bodies(
            comms_conn, cutoff=before(policy.campaign_body_days), now=now
        )
    return RetentionReport(
        phases=phases,
        roots={
            "legacy": root["checkpoint_ref"] if root is not None else None,
            "comms": comms_root,
        },
        outcome="ok",
    )


def _truncate_comms(conn: Any, cutoff: datetime) -> tuple[str | None, int]:
    """Phase 4 (B18): delete comms events strictly before the latest verified root.

    Under the COMMS append guard, so no append interleaves; the ``truncating`` flag is set
    only inside this transaction, and a trigger refuses every other delete. Checkpoints and
    the lineage record are never deleted.
    """
    with append_guard(COMMS):
        root = choose_root(
            conn, COMMS, cutoff=timeutil.iso(cutoff), public_keys=registry_public_for(conn)
        )
        if root is None:
            return None, 0
        with write_tx(conn):
            conn.execute("UPDATE maintenance_flags SET value = 1 WHERE name = 'truncating'")
            deleted = conn.execute(
                "DELETE FROM audit_events WHERE chain_epoch < ? OR (chain_epoch = ? AND chain_seq < ?)",
                (root["chain_epoch"], root["chain_epoch"], root["chain_seq"]),
            ).rowcount
            conn.execute("UPDATE maintenance_flags SET value = 0 WHERE name = 'truncating'")
    return str(root["checkpoint_ref"]), int(deleted)
