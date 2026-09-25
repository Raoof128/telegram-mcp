"""``cutover run|status`` (D39-PRE E7; A33, design §A.7).

``run`` is resumable from any persisted phase. When it reaches ``COMPLETE`` it releases the
daemon's write hold (R-E7: the latch set with reason ``CUTOVER_PENDING``); any other latch
reason is an integrity finding and stays set for ``audit repair`` and the owner.
"""

from __future__ import annotations

from typing import Any

from comms.core.audit.cutover import CutoverError, current_phase, run_cutover
from comms.core.audit.integrity import clear_degraded
from comms.runtime.operator.context import OperatorContext, OperatorHandler

__all__ = ["CUTOVER_HANDLERS", "CUTOVER_PENDING"]

CUTOVER_PENDING = "CUTOVER_PENDING"


def _held_for_cutover(conn: Any) -> bool:
    row = conn.execute("SELECT state, reason FROM audit_integrity WHERE id = 1").fetchone()
    return row is not None and row[0] == "degraded" and row[1] == CUTOVER_PENDING


def _run(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    legacy = ctx.require_legacy()
    try:
        phase = run_cutover(ctx.conn, legacy.port(), ctx.writer, now=ctx.clock())
    except CutoverError as refused:
        raise ValueError(f"cutover refused: {refused}") from None
    released = phase == "COMPLETE" and _held_for_cutover(ctx.conn)
    if released:
        clear_degraded(ctx.conn, now=ctx.clock())
    return {"phase": phase, "writes_released": released}


def _status(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    phase, cut, _digest = current_phase(ctx.conn)
    return {"phase": phase, "cutover": cut, "writes_held": _held_for_cutover(ctx.conn)}


CUTOVER_HANDLERS: dict[tuple[str, ...], OperatorHandler] = {
    ("cutover", "run"): _run,
    ("cutover", "status"): _status,
}
