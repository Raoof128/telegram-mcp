"""The owner's operator commands, served over the admin socket (D31; D39-PRE E7–E8; R-E3).

One table, ``OPERATOR_HANDLERS``, maps each ``OPERATOR_COMMANDS`` entry that runs in the daemon
to its handler. A registered command has a handler; a command without one is not registered
(R-E3, pinned by ``tests/cli/test_operator_completeness.py``). Replies carry metadata only:
purposes, versions, key ids, states, counts and codes; never material.

A handler refuses by raising ``ValueError`` with a fixed message; the admin router returns it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from comms.runtime.operator.audit import AUDIT_HANDLERS
from comms.runtime.operator.clients import CLIENT_HANDLERS
from comms.runtime.operator.context import LegacySide, OperatorContext, OperatorHandler
from comms.runtime.operator.cutover import CUTOVER_HANDLERS
from comms.runtime.operator.keys import KEY_HANDLERS

__all__ = ["OPERATOR_HANDLERS", "LegacySide", "OperatorContext", "operator_handler"]

OPERATOR_HANDLERS: Mapping[tuple[str, ...], OperatorHandler] = {
    **CLIENT_HANDLERS,
    **KEY_HANDLERS,
    **AUDIT_HANDLERS,
    **CUTOVER_HANDLERS,
}


def operator_handler(ctx: OperatorContext) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The admin socket's ``operator`` handler over one context."""

    def handle(args: dict[str, Any]) -> dict[str, Any]:
        command = tuple(args.get("command") or ())
        handler = OPERATOR_HANDLERS.get(command)
        if handler is None:
            raise ValueError("unknown operator command")
        return handler(ctx, args)

    return handle
