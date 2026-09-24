"""The closed dispatcher (comms v0.3 A4, A37): catalog names only, services only.

A name outside ``TOOL_CATALOG`` — including every retired Telegram and WhatsVault name —
answers ``TOOL_NOT_FOUND`` before anything else runs: no database access, no service,
no provider. Arguments are validated against the tool's input schema before the service
is resolved, and the service's result against the output schema before it is returned.
The error code travels in ``structuredContent``; the text is fixed and never names it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from comms.mcp.catalog import TOOL_CATALOG, ToolSpec
from comms.services.registry import ServiceRegistry

__all__ = ["AuthenticatedClient", "Dispatcher", "ToolResult"]

_ERROR_TEXT = "The request was refused."
_OK_TEXT = "Done."


@dataclass(frozen=True)
class AuthenticatedClient:
    client_ref: str  # cli_
    auth_kind: str  # "cml1" locally, "oauth" remotely (A33, A35)


@dataclass(frozen=True)
class ToolResult:
    structured: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_mcp(self) -> dict[str, Any]:
        if self.error_code is not None:
            return {
                "content": [{"type": "text", "text": _ERROR_TEXT}],
                "structuredContent": {"error": {"code": self.error_code}},
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": _OK_TEXT}],
            "structuredContent": dict(self.structured),
            "isError": False,
        }


class Dispatcher:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services
        self._tools: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_CATALOG}
        self._inputs = {s.name: Draft202012Validator(dict(s.input_schema)) for s in TOOL_CATALOG}
        self._outputs = {s.name: Draft202012Validator(dict(s.output_schema)) for s in TOOL_CATALOG}

    def call(
        self, client: AuthenticatedClient, name: str, arguments: Mapping[str, Any]
    ) -> ToolResult:
        spec = self._tools.get(name) if isinstance(name, str) else None
        if spec is None:
            return ToolResult(error_code="TOOL_NOT_FOUND")
        if not isinstance(arguments, Mapping) or not self._inputs[name].is_valid(dict(arguments)):
            return ToolResult(error_code="INVALID_ARGUMENT")
        result = self._services.resolve(spec.service)(client, dict(arguments))
        if not self._outputs[name].is_valid(result):
            return ToolResult(error_code="INTERNAL_ERROR")  # fail closed: never an unchecked shape
        return ToolResult(structured=result)
