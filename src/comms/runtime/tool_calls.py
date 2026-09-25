"""The admin socket's ``tool call`` command (comms v0.3 Task D30; A37).

The CLI's operations arrive here as ``{tool, arguments}`` and run through the one dispatcher,
as the installation owner's client (the admin peer is the owner). The answer carries the
request id the CLI minted, the result's ``op_`` ref, and the error code if refused — never a
trace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from comms.mcp.dispatch import AuthenticatedClient, Dispatcher

__all__ = ["OWNER_CLIENT", "tool_call_handler"]

# The owner's operations through the admin socket are recorded under this client ref.
OWNER_CLIENT = "cli_" + "o" * 26
_OWNER = AuthenticatedClient(client_ref=OWNER_CLIENT, auth_kind="admin_peer")


def tool_call_handler(dispatcher: Dispatcher) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    def handle(args: Mapping[str, Any]) -> dict[str, Any]:
        if set(args) != {"tool", "arguments"}:
            raise ValueError("tool call takes tool and arguments")
        tool, arguments = args["tool"], args["arguments"]
        if not isinstance(tool, str) or not isinstance(arguments, Mapping):
            raise ValueError("tool call takes a tool name and an arguments object")  # noqa: TRY004 -- the admin router maps ValueError to MALFORMED_REQUEST
        result = dispatcher.call(_OWNER, tool, dict(arguments))
        structured = dict(result.structured) if result.error_code is None else None
        return {
            "tool": tool,
            "request_id": arguments.get("request_id"),
            "op_ref": (structured or {}).get("op_ref"),
            "error": result.error_code,
            "result": structured,
        }

    return handle
