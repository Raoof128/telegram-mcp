"""``client add|rotate|disable`` and ``oauth approve`` (D27, D34; moved here in D39-PRE E7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from comms.core.auth import clients
from comms.runtime.operator.context import OperatorContext, OperatorHandler

__all__ = ["CLIENT_HANDLERS"]


def _add(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    ref = clients.add_client(
        ctx.conn, ctx.store, args["name"], now=ctx.clock(), helper_path=Path(args["helper_path"])
    )
    return {"client": ref}


def _rotate(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    clients.rotate_client(
        ctx.conn, ctx.store, args["client"], now=ctx.clock(), helper_path=Path(args["helper_path"])
    )
    return {"client": args["client"], "rotated": True}


def _disable(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    clients.disable_client(ctx.conn, args["client"])
    return {"client": args["client"], "disabled": True}


def _approve(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.oauth is None:
        raise ValueError("the remote listener is not configured")
    return {"owner_code": ctx.oauth.approvals.issue(), "valid_for_seconds": 300}


CLIENT_HANDLERS: dict[tuple[str, ...], OperatorHandler] = {
    ("client", "add"): _add,
    ("client", "rotate"): _rotate,
    ("client", "disable"): _disable,
    ("oauth", "approve"): _approve,
}
