"""The v0.3 constitutional cutover: a resumable state machine (spec A6, A7; design §A.7).

Core orchestrates; the legacy transport implements ``LegacyPort`` (R-003). Every phase is
persisted in ``cutover_state`` before the next begins, the database enforces the exact
next-state table and the immutability of the cut ref and legacy digest, and a replay
reuses what already exists and fails closed on any conflict.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from comms.core import refs, timeutil
from comms.core.storage.db import write_tx

__all__ = [
    "PHASES",
    "CutoverCrash",
    "CutoverError",
    "LegacyPort",
    "LegacySeal",
    "advance_legacy",
    "current_phase",
    "record_seal",
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


def _set_phase(conn: Any, phase: str, *, now: datetime, **fields: str) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with write_tx(conn):
        conn.execute(
            f"UPDATE cutover_state SET phase = ?, updated_at = ?{', ' + sets if sets else ''}"
            " WHERE id = 1",
            (phase, timeutil.iso(now), *fields.values()),
        )


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
