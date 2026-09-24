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

import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.audit.chain import CHAIN_COLUMNS, COMMS, ChainProfile, append_guard, event_mac
from comms.core.audit.integrity import latch_degraded, require_not_degraded
from comms.core.audit.retention_root import choose_root
from comms.core.audit.verify_all import LegacyVerify
from comms.core.audit.writer import AuditWriter
from comms.core.keys.slots import KeySlotStore, registry_public_for
from comms.core.maintenance.redaction import UNRESOLVED_STATES, redact_campaign_bodies
from comms.core.storage.db import write_tx

__all__ = [
    "CRASH_POINTS",
    "LegacyRetention",
    "RetentionCrash",
    "RetentionFailed",
    "RetentionPolicy",
    "RetentionReport",
    "purge_inbound",
    "purge_retired_keys",
    "redact_identities",
    "run_retention",
]


CRASH_POINTS = (
    "after_legacy",
    "after_comms_chain",
    "after_bodies",
    "after_inbound",
    "after_identities",
    "after_keys",
)


class RetentionCrash(BaseException):
    """Raised only by the ``crash_at`` seam, which production never supplies."""


class RetentionFailed(Exception):
    """A root's row does not recompute to its signed MAC: the latch is set, nothing deleted."""

    def __init__(self) -> None:
        super().__init__("AUDIT_INTEGRITY_DEGRADED")


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
    crash_at: str | None = None,
) -> RetentionReport:
    """Run every phase; each is its own transaction, so a crash between phases leaves each
    finished phase done and a rerun converges (``crash_at`` is a test seam: CRASH_POINTS)."""

    def crash(point: str) -> None:
        if crash_at == point:
            raise RetentionCrash(point)

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
    if root is not None:
        _require_root_row(
            comms_conn, legacy.conn, legacy.verify.profile, root, legacy.verify.key_for_epoch, now
        )
    phases["legacy_chain"] = legacy.truncate_chain_before(root) if root is not None else 0
    phases["legacy_receipts"] = legacy.purge_receipts(before(policy.receipt_days))
    phases["legacy_message_refs"] = legacy.purge_message_refs(
        before(policy.message_ref_days), now=now
    )
    crash("after_legacy")
    comms_root, phases["comms_chain"] = _truncate_comms(
        comms_conn, before(policy.audit_events_days), writer.keys.for_epoch, now
    )
    blocked = comms_root is None and _due(comms_conn, COMMS, before(policy.audit_events_days))
    crash("after_comms_chain")
    with write_tx(comms_conn):
        phases["campaign_bodies"] = redact_campaign_bodies(
            comms_conn, cutoff=before(policy.campaign_body_days), now=now
        )
    crash("after_bodies")
    with write_tx(comms_conn):
        phases["inbound_bodies"] = purge_inbound(
            comms_conn, cutoff=before(policy.campaign_body_days)
        )
    crash("after_inbound")
    with write_tx(comms_conn):
        phases["identities"] = redact_identities(
            comms_conn, cutoff=before(policy.identity_retention_days)
        )
    crash("after_identities")
    phases.update(purge_retired_keys(comms_conn, store))
    crash("after_keys")
    roots = {"legacy": root["checkpoint_ref"] if root is not None else None, "comms": comms_root}
    with writer.transaction() as tx:
        tx.append(
            "maintenance.retention_purge",
            payload={**phases, "comms_root": roots["comms"], "legacy_root": roots["legacy"]},
        )
    return RetentionReport(phases=phases, roots=roots, outcome="blocked" if blocked else "ok")


# A job in one of these states may still be delivered, retried or resolved.
_UNRESOLVED = ", ".join(f"'{s}'" for s in UNRESOLVED_STATES)
_ENDPOINTS = ("destinations", "contact_points")


def purge_inbound(conn: Any, *, cutoff: datetime) -> int:
    """Phase 5b (A15's body rule): retained inbound bodies older than the body window — Bot API
    updates, MTProto updates, and completed webhook bodies. An unfinished inbox row is never
    purged: its fan-out has not run. Caller's transaction."""
    limit = timeutil.iso(cutoff)
    removed = conn.execute("DELETE FROM bot_updates WHERE received_at < ?", (limit,)).rowcount
    removed += conn.execute("DELETE FROM user_updates WHERE received_at < ?", (limit,)).rowcount
    removed += conn.execute(
        "DELETE FROM webhook_inbox WHERE completed_at IS NOT NULL AND completed_at < ?", (limit,)
    ).rowcount
    return int(removed)


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


def _due(conn: Any, profile: ChainProfile, cutoff: datetime) -> bool:
    """Whether any retained event is older than the cutoff (so truncation was wanted)."""
    stamps = conn.execute(f"SELECT ts FROM {profile.events_table}").fetchall()
    return any(timeutil.instant(row[0]) < cutoff for row in stamps)


def _require_root_row(
    comms_conn: Any,
    conn: Any,
    profile: ChainProfile,
    root: Mapping[str, Any],
    key_for_epoch: Callable[[int], bytes],
    now: datetime,
) -> None:
    """The root's row must recompute to the signed MAC; otherwise latch and delete nothing."""
    names = (*profile.event_columns, *CHAIN_COLUMNS)
    row = conn.execute(
        f"SELECT {', '.join(names)} FROM {profile.events_table} WHERE chain_epoch = ? AND chain_seq = ?",
        (root["chain_epoch"], root["chain_seq"]),
    ).fetchone()
    record = dict(zip(names, row, strict=True)) if row is not None else None
    if record is None or not hmac.compare_digest(
        event_mac(
            profile,
            key_for_epoch(record["chain_epoch"]),
            chain_epoch=record["chain_epoch"],
            chain_seq=record["chain_seq"],
            prev_event_mac=record["prev_event_mac"],
            event=record,
        ),
        root["last_event_mac"],
    ):
        latch_degraded(comms_conn, reason="RETENTION_ROOT_MISMATCH", now=now)
        raise RetentionFailed


def _truncate_comms(
    conn: Any, cutoff: datetime, key_for_epoch: Callable[[int], bytes], now: datetime
) -> tuple[str | None, int]:
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
        _require_root_row(conn, conn, COMMS, root, key_for_epoch, now)
        with write_tx(conn):
            conn.execute("UPDATE maintenance_flags SET value = 1 WHERE name = 'truncating'")
            deleted = conn.execute(
                "DELETE FROM audit_events WHERE chain_epoch < ? OR (chain_epoch = ? AND chain_seq < ?)",
                (root["chain_epoch"], root["chain_epoch"], root["chain_seq"]),
            ).rowcount
            conn.execute("UPDATE maintenance_flags SET value = 0 WHERE name = 'truncating'")
    return str(root["checkpoint_ref"]), int(deleted)
