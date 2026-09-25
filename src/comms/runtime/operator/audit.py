"""``audit verify [--all]`` and ``audit repair`` (D39-PRE E7; A7, A8, G7).

``verify`` derives the comms chain's integrity against its anchor (the startup check).
``verify --all`` walks the legacy chain, the lineage and the comms chain (``verify_all``).
``repair`` advances a stale anchor only after proving it names a retained ancestor.
"""

from __future__ import annotations

from typing import Any

from comms.core.audit.anchor import COMMS_ANCHOR, derive_integrity
from comms.core.audit.chain import COMMS
from comms.core.audit.repair import RepairRefused, audit_repair, verify_for_anchor_repair
from comms.core.audit.verify_all import VerifyKeys, verify_all
from comms.core.keys.slots import registry_public_for
from comms.runtime.operator.context import OperatorContext, OperatorHandler

__all__ = ["AUDIT_HANDLERS"]


def _verify(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    keys, anchor = ctx.writer.keys, ctx.writer.anchor_path
    public_for = registry_public_for(ctx.conn)
    if not args.get("all"):
        integrity = derive_integrity(
            ctx.conn, COMMS, COMMS_ANCHOR, keys.for_epoch, anchor, public_for=public_for
        )
        return {"comms": integrity, "ok": integrity == "CLEAN"}
    legacy = ctx.require_legacy()
    report = verify_all(
        ctx.conn,
        legacy.conn,
        VerifyKeys(
            legacy=legacy.verify,
            comms_key_for_epoch=keys.for_epoch,
            comms_anchor_path=anchor,
            comms_public_for=public_for,
        ),
    )
    return {
        "ok": report.ok,
        "legacy": report.legacy,
        "lineage": report.lineage,
        "comms": report.comms,
        "problems": list(report.problems),
    }


def _repair(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    plan = verify_for_anchor_repair(
        ctx.conn,
        COMMS,
        ctx.writer.keys.for_epoch,
        registry_public_for(ctx.conn),
        ctx.writer.anchor_path,
    )
    if isinstance(plan, RepairRefused):
        raise ValueError(f"repair refused: {plan.code}")  # noqa: TRY004 -- uniform ValueError: the admin router's refusal
    audit_repair(ctx.writer, plan)
    return {
        "repaired": True,
        "from": [plan.anchor_epoch, plan.anchor_seq],
        "to": [plan.head_epoch, plan.head_seq],
    }


AUDIT_HANDLERS: dict[tuple[str, ...], OperatorHandler] = {
    ("audit", "verify"): _verify,
    ("audit", "repair"): _repair,
}
