"""``comms doctor`` (comms v0.3 Task B29): health findings, never material.

Checks: the key inventory (each required purpose ACTIVE, its material loading and its ID
recomputing), orphan slots, historical-key coverage (every checkpoint signer and every
commitment key still resolvable), overdue maintenance, both audit chains, the lineage,
credential presence (metadata only), the integrity latch, and the cutover phase.

A finding carries a fixed code, the subject it concerns (a purpose, a phase — never a
value), and a fixed sentence. Nothing here reads a secret into the output.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from comms.core import timeutil
from comms.core.audit import cutover
from comms.core.audit.chain import COMMS, ChainError, verify_chain, verify_checkpoints
from comms.core.audit.integrity import is_degraded
from comms.core.audit.verify_all import LegacyVerify, _root_of
from comms.core.audit.writer import SlotChainKeys
from comms.core.credentials import CONFIRMED_IN_OPERATION, is_confirmed
from comms.core.keys.purposes import PURPOSES
from comms.core.keys.rotate import find_orphans
from comms.core.keys.slots import KeySlotError, KeySlotStore, load_active, registry_public_for

__all__ = ["REQUIRED_KEYS", "Finding", "doctor"]

REQUIRED_KEYS = ("audit-chain-key", "audit-checkpoint-key", "campaign-commit-key", "backup-key")
MAINTENANCE_EVERY = timedelta(days=7)
_CREDENTIALS = tuple(sorted(n for n, p in PURPOSES.items() if p.rotation == "staged"))


@dataclass(frozen=True)
class Finding:
    code: str
    subject: str | None
    detail: str


def _keys(conn: Any, store: KeySlotStore) -> list[Finding]:
    out = []
    for purpose in REQUIRED_KEYS:
        try:
            load_active(conn, store, purpose)
        except KeySlotError as exc:
            missing = "no active key" in str(exc)
            out.append(
                Finding(
                    "KEY_MISSING" if missing else "KEY_INVALID",
                    purpose,
                    "no active version"
                    if missing
                    else "the active version does not load or recompute",
                )
            )
    for purpose, versions in find_orphans(conn, store).items():
        out.append(
            Finding("ORPHAN_KEY_SLOT", purpose, f"{len(versions)} staged slot(s) never activated")
        )
    return out


def _coverage(conn: Any) -> list[Finding]:
    signers = {r[0] for r in conn.execute("SELECT DISTINCT signing_key_id FROM audit_checkpoints")}
    registered = {r[0] for r in conn.execute("SELECT key_id FROM verification_keys")}
    commit_keys = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT campaign_commit_key_id FROM generations WHERE campaign_commit_key_id IS NOT NULL"
        )
    }
    slotted = {
        r[0]
        for r in conn.execute(
            "SELECT key_id FROM key_slots WHERE purpose = 'campaign-commit-key' AND state <> 'DESTROYED'"
        )
    }
    out = []
    if signers - registered:
        out.append(
            Finding(
                "KEY_COVERAGE_GAP",
                "audit-checkpoint-key",
                "a retained checkpoint's signer is not registered",
            )
        )
    if commit_keys - slotted:
        out.append(
            Finding(
                "KEY_COVERAGE_GAP", "campaign-commit-key", "a retained commitment's key is gone"
            )
        )
    return out


def _maintenance(conn: Any, now: datetime) -> list[Finding]:
    row = conn.execute(
        "SELECT ts FROM audit_events WHERE kind = 'maintenance.retention_purge'"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
    ).fetchone()
    if row is None or timeutil.utc(now) - timeutil.instant(row[0]) > MAINTENANCE_EVERY:
        return [
            Finding("MAINTENANCE_OVERDUE", None, "retention has not run in the last seven days")
        ]
    return []


def _chains(
    conn: Any, store: KeySlotStore, legacy_conn: Any, legacy: LegacyVerify | None
) -> list[Finding]:
    out = []
    try:
        public_for = registry_public_for(conn)
        verify_checkpoints(conn, COMMS, public_for)
        verify_chain(
            conn,
            COMMS,
            SlotChainKeys(conn, store).for_epoch,
            root=_root_of(conn, COMMS),
            public_for=public_for,
        )
    except (ChainError, KeySlotError):
        out.append(Finding("COMMS_CHAIN_INVALID", "comms", "the comms audit chain does not verify"))
    if legacy_conn is not None and legacy is not None:
        try:
            verify_checkpoints(legacy_conn, legacy.profile, legacy.public_for)
            verify_chain(
                legacy_conn,
                legacy.profile,
                legacy.key_for_epoch,
                root=_root_of(legacy_conn, legacy.profile),
            )
        except ChainError:
            out.append(
                Finding("LEGACY_CHAIN_INVALID", "legacy", "the legacy audit chain does not verify")
            )
    return out


def _lineage(conn: Any) -> list[Finding]:
    names = (*cutover.LINEAGE_COLUMNS, "lineage_digest")
    for row in conn.execute(f"SELECT {', '.join(names)} FROM audit_lineage").fetchall():
        record = dict(zip(cutover.LINEAGE_COLUMNS, row[:-1], strict=True))
        if not hmac.compare_digest(cutover.lineage_digest(record), row[-1]):
            return [Finding("LINEAGE_INVALID", "lineage", "the lineage record does not recompute")]
    return []


def _credentials(conn: Any) -> list[Finding]:
    active = dict(
        conn.execute("SELECT purpose, version FROM key_slots WHERE state = 'ACTIVE'").fetchall()
    )
    missing = [
        Finding("CREDENTIAL_NOT_CONFIGURED", purpose, "no active credential")
        for purpose in _CREDENTIALS
        if purpose not in active
    ]
    unconfirmed = [
        Finding("CREDENTIAL_UNCONFIRMED", purpose, "not yet confirmed by Meta in operation")
        for purpose in CONFIRMED_IN_OPERATION
        if purpose in active and not is_confirmed(conn, purpose, int(active[purpose]))
    ]
    return missing + unconfirmed


def doctor(
    conn: Any,
    store: KeySlotStore,
    *,
    now: datetime,
    legacy_conn: Any = None,
    legacy: LegacyVerify | None = None,
) -> list[Finding]:
    findings = [*_keys(conn, store), *_coverage(conn), *_maintenance(conn, now)]
    findings += _chains(conn, store, legacy_conn, legacy)
    findings += _lineage(conn)
    findings += _credentials(conn)
    if is_degraded(conn):
        findings.append(
            Finding("AUDIT_DEGRADED", None, "the audit integrity latch is set; run audit repair")
        )
    phase = cutover.current_phase(conn)[0]
    if phase != "COMPLETE":
        findings.append(Finding("CUTOVER_INCOMPLETE", phase, "the v0.3 cutover has not completed"))
    return findings
