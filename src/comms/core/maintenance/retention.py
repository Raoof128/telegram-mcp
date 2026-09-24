"""The retention runner (comms v0.3 A15, design §B.8): foreign-key order, fail closed.

The legacy phases (Task B17), in the order the legacy foreign keys force (N8):

1. exposure rows older than ``exposure_ledger_days``;
2. legacy chain truncation strictly before the latest verified checkpoint at or before
   ``audit_events_days`` (Task B3) — checkpoints, the final ``V0_3_CUTOVER`` seal and its
   marker row are never deleted;
3. receipts older than ``receipt_days`` once nothing references them (their exposure and
   audit rows are gone, so the foreign keys make this mechanical);
4. ``message_refs`` older than ``message_ref_days`` that no live cursor names.

Core never reads the Telegram schema: the transport supplies a ``LegacyRetention``.
Retention refuses while the audit integrity latch is set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.audit.integrity import require_not_degraded
from comms.core.audit.retention_root import choose_root
from comms.core.audit.verify_all import LegacyVerify
from comms.core.audit.writer import AuditWriter

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
    return RetentionReport(
        phases=phases,
        roots={"legacy": root["checkpoint_ref"] if root is not None else None},
        outcome="ok",
    )
