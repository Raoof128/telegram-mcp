"""``audit repair``: advance a stale anchor only after proving it names an ancestor (G7).

Repair is the one way out of the integrity latch. It requires, in order:

1. the anchor file to exist and to authenticate under its own epoch's chain key;
2. the anchor's ``(epoch, seq, event_id, event_mac)`` to name a **retained** row of this
   chain — not ahead of the head, not an invented row;
3. every retained link to verify, epoch seals and a truncation root included;
4. the checkpoints and the lineage record to verify.

Only then does ``audit_repair`` append ``admin.audit_repair`` through the writer, which
anchors exactly the new head under its lock, and clear the latch.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from comms.core.audit import cutover
from comms.core.audit.anchor import COMMS_ANCHOR, AnchorError, read_anchor
from comms.core.audit.chain import (
    COMMS,
    ChainError,
    ChainProfile,
    head,
    verify_chain,
    verify_checkpoints,
)
from comms.core.audit.integrity import clear_degraded
from comms.core.audit.verify_all import _root_of
from comms.core.audit.writer import AuditWriter

__all__ = ["RepairPlan", "RepairRefused", "audit_repair", "verify_for_anchor_repair"]


@dataclass(frozen=True)
class RepairPlan:
    anchor_epoch: int
    anchor_seq: int
    head_epoch: int
    head_seq: int


@dataclass(frozen=True)
class RepairRefused:
    code: str  # ANCHOR_MISSING | ANCHOR_UNAUTHENTICATED | ANCHOR_AHEAD | ANCHOR_NOT_IN_CHAIN
    #           | CHAIN_INVALID | LINEAGE_INVALID | CHAIN_EMPTY


def verify_for_anchor_repair(
    conn: Any,
    profile: ChainProfile,
    key_for_epoch: Callable[[int], bytes],
    public_for: Callable[[str], bytes | None],
    anchor_path: Path,
) -> RepairPlan | RepairRefused:
    if profile is not COMMS:
        return RepairRefused("CHAIN_INVALID")  # only the comms chain is repaired here
    path = Path(anchor_path)
    if not path.exists():
        return RepairRefused("ANCHOR_MISSING")
    try:
        claimed = json.loads(path.read_text(encoding="utf-8"))
        anchored = read_anchor(COMMS_ANCHOR, path, key_for_epoch(int(claimed["chain_epoch"])))
    except (AnchorError, ValueError, KeyError, TypeError, LookupError):
        return RepairRefused("ANCHOR_UNAUTHENTICATED")
    current = head(conn, profile)
    if current is None:
        return RepairRefused("CHAIN_EMPTY")
    position = (anchored["chain_epoch"], anchored["chain_seq"])
    if position > (current["chain_epoch"], current["chain_seq"]):
        return RepairRefused("ANCHOR_AHEAD")
    row = conn.execute(
        f"SELECT event_id, event_mac FROM {profile.events_table} WHERE chain_epoch = ? AND chain_seq = ?",
        position,
    ).fetchone()
    if (
        row is None
        or row[0] != anchored["event_id"]
        or not hmac.compare_digest(row[1], anchored["event_mac"])
    ):
        return RepairRefused("ANCHOR_NOT_IN_CHAIN")
    try:
        verify_checkpoints(conn, profile, public_for)
        verify_chain(
            conn, profile, key_for_epoch, root=_root_of(conn, profile), public_for=public_for
        )
    except ChainError:
        return RepairRefused("CHAIN_INVALID")
    for lineage in conn.execute(
        f"SELECT {', '.join(cutover.LINEAGE_COLUMNS)}, lineage_digest FROM audit_lineage"
    ).fetchall():
        record = dict(zip(cutover.LINEAGE_COLUMNS, lineage[:-1], strict=True))
        if not hmac.compare_digest(cutover.lineage_digest(record), lineage[-1]):
            return RepairRefused("LINEAGE_INVALID")
    return RepairPlan(position[0], position[1], current["chain_epoch"], current["chain_seq"])


def audit_repair(writer: AuditWriter, plan: RepairPlan | RepairRefused) -> None:
    """Advance the anchor to the head (through the writer) and clear the latch."""
    if not isinstance(plan, RepairPlan):
        raise ValueError("audit repair was refused by its verifier")  # noqa: TRY004 -- uniform refusal
    with writer.transaction() as tx:
        tx.append(
            "admin.audit_repair",
            payload={
                "anchor_epoch": plan.anchor_epoch,
                "anchor_seq": plan.anchor_seq,
                "head_epoch": plan.head_epoch,
                "head_seq": plan.head_seq,
            },
        )
    clear_degraded(writer.conn, now=writer.now())
