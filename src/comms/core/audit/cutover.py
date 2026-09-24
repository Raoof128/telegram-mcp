"""The v0.3 constitutional cutover: a resumable state machine (spec A6, A7; design §A.7).

Core orchestrates; the legacy transport implements ``LegacyPort`` (R-003). Every phase is
persisted in ``cutover_state`` before the next begins, the database enforces the exact
next-state table and the immutability of the cut ref and legacy digest, and a replay
reuses what already exists and fails closed on any conflict.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from comms.core import refs, timeutil
from comms.core.audit.chain import COMMS, genesis_mac, head
from comms.core.canonical import jcs_dumps
from comms.core.storage.db import write_tx

if TYPE_CHECKING:
    from comms.core.audit.writer import AuditWriter

__all__ = [
    "LINEAGE_COLUMNS",
    "PHASES",
    "CutoverCrash",
    "CutoverError",
    "LegacyPort",
    "LegacySeal",
    "advance_comms",
    "advance_legacy",
    "current_phase",
    "lineage_digest",
    "record_seal",
    "run_cutover",
]

PHASES = (
    "NONE",
    "CUTOVER_ENTERED",
    "LEGACY_DRAINED",
    "LEGACY_VERIFIED",
    "LEGACY_SEALED",
    "LEGACY_ANCHORED",
    "COMMS_GENESIS",
    "COMMS_ANCHORED",
    "LEGACY_CLIENT_AUTH_REVOKED",
    "COMPLETE",
)


# Every audit_lineage column except lineage_digest, which is SHA-256 over their JCS.
LINEAGE_COLUMNS = (
    "cutover_ref",
    "legacy_chain_domain",
    "legacy_final_epoch",
    "legacy_final_head",
    "legacy_checkpoint_digest",
    "legacy_checkpoint_key_id",
    "comms_chain_domain",
    "comms_genesis_digest",
    "comms_first_epoch",
    "created_at",
)
COMMS_FIRST_EPOCH = 1


class CutoverError(Exception):
    """The cutover was refused. Fixed messages."""


class CutoverCrash(BaseException):
    """Raised only by the ``crash_at`` seam, which production never supplies."""


@dataclass(frozen=True)
class LegacySeal:
    final_epoch: int
    final_head: str  # the sealing checkpoint's last_event_mac
    checkpoint_digest: str
    checkpoint_key_id: str


class LegacyPort(Protocol):
    chain_domain: str  # the legacy chain's event domain, recorded in the lineage

    def close_ingress(self) -> None: ...
    def drained(self) -> bool: ...
    def verify(self) -> bool: ...
    def sealed_record(self) -> LegacySeal | None: ...
    def seal(self, *, now: datetime) -> LegacySeal: ...
    def refresh_anchor(self, *, now: datetime) -> None: ...
    def revoke_client_auth(self, *, now: datetime) -> tuple[int, int]: ...


def current_phase(conn: Any) -> tuple[str, str | None, str | None]:
    row = conn.execute(
        "SELECT phase, cutover_ref, legacy_checkpoint_digest FROM cutover_state WHERE id = 1"
    ).fetchone()
    return str(row[0]), row[1], row[2]


def _phase_in_tx(conn: Any, phase: str, *, now: datetime, **fields: str) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE cutover_state SET phase = ?, updated_at = ?{', ' + sets if sets else ''}"
        " WHERE id = 1",
        (phase, timeutil.iso(now), *fields.values()),
    )


def _set_phase(conn: Any, phase: str, *, now: datetime, **fields: str) -> None:
    with write_tx(conn):
        _phase_in_tx(conn, phase, now=now, **fields)


def record_seal(conn: Any, seal: LegacySeal, *, now: datetime) -> None:
    """Persist the legacy seal's digest and move to LEGACY_SEALED; a different digest fails closed."""
    phase, _cut, existing = current_phase(conn)
    if existing is not None and existing != seal.checkpoint_digest:
        raise CutoverError("lineage conflict")
    if phase == "LEGACY_VERIFIED":
        _set_phase(conn, "LEGACY_SEALED", now=now, legacy_checkpoint_digest=seal.checkpoint_digest)


def advance_legacy(
    conn: Any,
    legacy: LegacyPort,
    *,
    now: datetime,
    drain_timeout_s: float = 30.0,
    poll_s: float = 0.01,
    crash_at: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run NONE → … → LEGACY_ANCHORED, resuming from the persisted phase."""

    def crash(point: str) -> None:
        if crash_at == point:
            raise CutoverCrash(point)

    phase, _cut, _digest = current_phase(conn)
    if phase == "NONE":
        _set_phase(conn, "CUTOVER_ENTERED", now=now, cutover_ref=refs.mint("cutover"))
        crash("after_CUTOVER_ENTERED")
        phase = "CUTOVER_ENTERED"
    if phase == "CUTOVER_ENTERED":
        legacy.close_ingress()
        deadline = time.monotonic() + drain_timeout_s
        while not legacy.drained():
            if time.monotonic() >= deadline:
                raise CutoverError("legacy work did not drain")
            sleep(poll_s)
        _set_phase(conn, "LEGACY_DRAINED", now=now)
        crash("after_LEGACY_DRAINED")
        phase = "LEGACY_DRAINED"
    if phase == "LEGACY_DRAINED":
        if legacy.sealed_record() is None and not legacy.verify():
            raise CutoverError("legacy chain did not verify")
        _set_phase(conn, "LEGACY_VERIFIED", now=now)
        crash("after_LEGACY_VERIFIED")
        phase = "LEGACY_VERIFIED"
    if phase == "LEGACY_VERIFIED":
        seal = legacy.sealed_record()
        if seal is None:
            seal = legacy.seal(now=now)  # one legacy transaction: marker + checkpoint + sealed
            crash("after_legacy_seal_commit")
        record_seal(conn, seal, now=now)
        crash("after_LEGACY_SEALED")
        phase = "LEGACY_SEALED"
    if phase == "LEGACY_SEALED":
        seal = legacy.sealed_record()
        if seal is None or seal.checkpoint_digest != current_phase(conn)[2]:
            raise CutoverError("lineage conflict")
        legacy.refresh_anchor(now=now)
        _set_phase(conn, "LEGACY_ANCHORED", now=now)
        crash("after_LEGACY_ANCHORED")
        phase = "LEGACY_ANCHORED"
    return phase


def lineage_digest(columns: Mapping[str, Any]) -> str:
    """SHA-256 over the JCS of exactly ``LINEAGE_COLUMNS``."""
    if set(columns) != set(LINEAGE_COLUMNS):
        raise ValueError("lineage columns refused")
    return hashlib.sha256(jcs_dumps(dict(columns))).hexdigest()


def _comms_domain() -> str:
    return COMMS.event_domain.rstrip(b"\0").decode()


def _expected_lineage(cut: str, seal: LegacySeal, legacy: LegacyPort) -> dict[str, Any]:
    return {
        "cutover_ref": cut,
        "legacy_chain_domain": legacy.chain_domain,
        "legacy_final_epoch": seal.final_epoch,
        "legacy_final_head": seal.final_head,
        "legacy_checkpoint_digest": seal.checkpoint_digest,
        "legacy_checkpoint_key_id": seal.checkpoint_key_id,
        "comms_chain_domain": _comms_domain(),
        "comms_genesis_digest": genesis_mac(COMMS, COMMS_FIRST_EPOCH),
        "comms_first_epoch": COMMS_FIRST_EPOCH,
    }


def _current_seal(conn: Any, legacy: LegacyPort) -> LegacySeal:
    """The legacy seal, which must be the one this cutover recorded."""
    seal = legacy.sealed_record()
    if seal is None or seal.checkpoint_digest != current_phase(conn)[2]:
        raise CutoverError("lineage conflict")
    return seal


def _check_lineage(conn: Any, cut: str, legacy: LegacyPort) -> dict[str, Any]:
    """The one lineage row, equal field by field to this seal, with a digest that recomputes."""
    expected = _expected_lineage(cut, _current_seal(conn, legacy), legacy)
    cur = conn.execute(f"SELECT {', '.join(LINEAGE_COLUMNS)}, lineage_digest FROM audit_lineage")
    rows = [dict(zip((*LINEAGE_COLUMNS, "lineage_digest"), r, strict=True)) for r in cur.fetchall()]
    if len(rows) != 1:
        raise CutoverError("lineage conflict")
    (row,) = rows
    stored = row.pop("lineage_digest")
    if any(row[k] != v for k, v in expected.items()) or lineage_digest(row) != stored:
        raise CutoverError("lineage conflict")
    return row


def advance_comms(
    conn: Any,
    legacy: LegacyPort,
    writer: AuditWriter,
    *,
    now: datetime,
    crash_at: str | None = None,
) -> str:
    """Run LEGACY_ANCHORED → … → COMPLETE, resuming from the persisted phase.

    COMMS_GENESIS is one audited transaction: the lineage row, the comms chain's first
    event (``system.audit_cutover``, bound to the lineage digest) and the phase. The
    writer anchors that head before the phase moves to COMMS_ANCHORED.
    """

    def crash(point: str) -> None:
        if crash_at == point:
            raise CutoverCrash(point)

    phase, cut, _digest = current_phase(conn)
    if phase in PHASES[: PHASES.index("LEGACY_ANCHORED")]:
        raise CutoverError("the legacy side is not anchored")
    assert cut is not None  # set at CUTOVER_ENTERED, immutable after
    if phase == "LEGACY_ANCHORED":
        seal = _current_seal(conn, legacy)
        if (
            head(conn, COMMS) is not None
            or conn.execute("SELECT count(*) FROM audit_lineage").fetchone()[0]
        ):
            raise CutoverError("comms chain is not empty")
        row = {**_expected_lineage(cut, seal, legacy), "created_at": timeutil.iso(now)}
        digest = lineage_digest(row)
        with writer.transaction() as tx:
            names = (*LINEAGE_COLUMNS, "lineage_digest")
            tx.conn.execute(
                f"INSERT INTO audit_lineage ({', '.join(names)})"
                f" VALUES ({', '.join('?' for _ in names)})",
                (*(row[k] for k in LINEAGE_COLUMNS), digest),
            )
            tx.append(
                "system.audit_cutover",
                subject_ref=cut,
                subject_digest=digest,
                payload={
                    "legacy_checkpoint_digest": seal.checkpoint_digest,
                    "legacy_final_epoch": seal.final_epoch,
                    "legacy_final_head": seal.final_head,
                    "legacy_checkpoint_key_id": seal.checkpoint_key_id,
                    "comms_chain_domain": _comms_domain(),
                    "comms_epoch": COMMS_FIRST_EPOCH,
                    "comms_audit_key_id": writer.key_id(),
                },
            )
            _phase_in_tx(tx.conn, "COMMS_GENESIS", now=now)
        crash("after_COMMS_GENESIS")  # the writer has already anchored this head
        _set_phase(conn, "COMMS_ANCHORED", now=now)
        crash("after_COMMS_ANCHORED")
        phase = "COMMS_ANCHORED"
    if phase == "COMMS_GENESIS":
        _check_lineage(conn, cut, legacy)
        writer.refresh_anchor()
        _set_phase(conn, "COMMS_ANCHORED", now=now)
        crash("after_COMMS_ANCHORED")
        phase = "COMMS_ANCHORED"
    if phase == "COMMS_ANCHORED":
        _check_lineage(conn, cut, legacy)
        revoked, security_epoch = legacy.revoke_client_auth(now=now)  # idempotent
        crash("after_legacy_revoke")
        with writer.transaction() as tx:
            tx.append(
                "system.legacy_client_auth_revoked",
                subject_ref=cut,
                payload={"revoked_count": revoked, "security_epoch": security_epoch},
            )
            _phase_in_tx(tx.conn, "LEGACY_CLIENT_AUTH_REVOKED", now=now)
        crash("after_LEGACY_CLIENT_AUTH_REVOKED")
        phase = "LEGACY_CLIENT_AUTH_REVOKED"
    if phase == "LEGACY_CLIENT_AUTH_REVOKED":
        _set_phase(conn, "COMPLETE", now=now)
        phase = "COMPLETE"
    return phase


def run_cutover(
    conn: Any,
    legacy: LegacyPort,
    writer: AuditWriter,
    *,
    now: datetime,
    crash_at: str | None = None,
) -> str:
    """The whole cutover, resumable from any persisted phase; ``crash_at`` is a test seam."""
    advance_legacy(conn, legacy, now=now, crash_at=crash_at)
    return advance_comms(conn, legacy, writer, now=now, crash_at=crash_at)
