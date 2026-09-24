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
from comms.core.keys.slots import KeySlotStore, registry_public_for
from comms.core.maintenance.redaction import UNRESOLVED_STATES, redact_campaign_bodies
from comms.core.storage.db import write_tx

__all__ = [
    "LegacyRetention",
    "RetentionPolicy",
    "RetentionReport",
    "purge_retired_keys",
    "redact_identities",
    "run_retention",
]


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
    store: KeySlotStore,
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
    with write_tx(comms_conn):
        phases["identities"] = redact_identities(
            comms_conn, cutoff=before(policy.identity_retention_days)
        )
    phases.update(purge_retired_keys(comms_conn, store))
    roots = {"legacy": root["checkpoint_ref"] if root is not None else None, "comms": comms_root}
    with writer.transaction() as tx:
        tx.append(
            "maintenance.retention_purge",
            payload={**phases, "comms_root": roots["comms"], "legacy_root": roots["legacy"]},
        )
    return RetentionReport(phases=phases, roots=roots, outcome="ok")


# A job in one of these states may still be delivered, retried or resolved.
_UNRESOLVED = ", ".join(f"'{s}'" for s in UNRESOLVED_STATES)
_ENDPOINTS = ("destinations", "contact_points")


def redact_identities(conn: Any, *, cutoff: datetime) -> int:
    """Phase 6 (B20): identities whose every endpoint was disabled before the cutoff.

    The identity and its endpoints' ``platform_identity`` become ``redacted:<id>`` (unique
    per row); never while an unresolved job references the identity. Caller's transaction.
    """
    limit = timeutil.iso(cutoff)
    rows = conn.execute(
        "SELECT i.id FROM delivery_identities i WHERE i.identity NOT LIKE 'redacted:%'"
        " AND EXISTS (SELECT 1 FROM destinations WHERE identity_id = i.id"
        "   UNION ALL SELECT 1 FROM contact_points WHERE identity_id = i.id)"
        " AND NOT EXISTS (SELECT 1 FROM destinations WHERE identity_id = i.id"
        "   AND (enabled = 1 OR disabled_at IS NULL OR disabled_at >= ?))"
        " AND NOT EXISTS (SELECT 1 FROM contact_points WHERE identity_id = i.id"
        "   AND (enabled = 1 OR disabled_at IS NULL OR disabled_at >= ?))"
        f" AND NOT EXISTS (SELECT 1 FROM delivery_jobs WHERE identity_id = i.id AND state IN ({_UNRESOLVED}))",
        (limit, limit),
    ).fetchall()
    for (identity_id,) in rows:
        marker = f"redacted:{identity_id}"
        conn.execute(
            "UPDATE delivery_identities SET identity = ? WHERE id = ?", (marker, identity_id)
        )
        for table in _ENDPOINTS:
            conn.execute(
                f"UPDATE {table} SET platform_identity = ? WHERE identity_id = ?",
                (marker, identity_id),
            )
    return len(rows)


def purge_retired_keys(conn: Any, store: KeySlotStore) -> dict[str, int]:
    """Phase 7 (B20): retired key material nothing retained still needs.

    - a retired checkpoint signer's public half, once no retained checkpoint names it;
    - a retired ``audit-chain-key`` secret, once its epoch is truncated (G8);
    - a retired ``campaign-commit-key`` secret, once no generation's commitment names it.

    Rows are marked ``DESTROYED`` in one transaction; the files are unlinked only after it
    commits, so a rollback never loses a secret.
    """
    with write_tx(conn):
        public = conn.execute(
            "DELETE FROM verification_keys WHERE purpose = 'audit-checkpoint-key'"
            " AND trust_state <> 'ACTIVE'"
            " AND key_id NOT IN (SELECT signing_key_id FROM audit_checkpoints)"
        ).rowcount
        oldest = conn.execute("SELECT min(chain_epoch) FROM audit_events").fetchone()[0]
        doomed = []
        if oldest is not None:
            doomed += [
                ("audit-chain-key", int(r[0]))
                for r in conn.execute(
                    "SELECT version FROM key_slots WHERE purpose = 'audit-chain-key'"
                    " AND state = 'RETIRED' AND version < ?",
                    (int(oldest),),
                )
            ]
        doomed += [
            ("campaign-commit-key", int(r[0]))
            for r in conn.execute(
                "SELECT version FROM key_slots WHERE purpose = 'campaign-commit-key' AND state = 'RETIRED'"
                " AND key_id NOT IN (SELECT campaign_commit_key_id FROM generations"
                "   WHERE campaign_commit_key_id IS NOT NULL)"
            )
        ]
        for purpose, version in doomed:
            conn.execute(
                "UPDATE key_slots SET state = 'DESTROYED' WHERE purpose = ? AND version = ?",
                (purpose, version),
            )
    for purpose, version in doomed:
        store.destroy(purpose, version)
    return {"public_keys": int(public), "secrets": len(doomed)}


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
