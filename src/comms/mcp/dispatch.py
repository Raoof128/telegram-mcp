"""The closed dispatcher (comms v0.3 A4, A37): catalog names only, services only.

A name outside ``TOOL_CATALOG`` — including every retired Telegram and WhatsVault name —
answers ``TOOL_NOT_FOUND`` before anything else runs: no database access, no service,
no provider. Arguments are validated against the tool's input schema before the service
is resolved, and the service's result against the output schema before it is returned.
The error code travels in ``structuredContent``; the text is fixed and never names it.

D26: the service runs through ``ServiceRegistry.call``, so a refusal is a structured error and
anything unexpected is ``INTERNAL_ERROR`` with nothing of it attached. No write accepts a
``ctx_`` handle or a ``cur_`` cursor anywhere in its arguments (A32: context is never
authority). A result carries ``next_actions`` — follow-up tools suggested, never taken.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from comms.mcp.catalog import TOOL_CATALOG, ToolSpec
from comms.services.errors import CommsError
from comms.services.registry import ServiceRegistry

__all__ = ["AuthenticatedClient", "Dispatcher", "ToolResult", "next_actions"]

_ERROR_TEXT = "The request was refused."
_OK_TEXT = "Done."
_HANDLE = re.compile(r"(?:ctx|cur)_[a-z2-7]{26}(?:\.[0-9a-f]{32})?\Z")
_MAX_ACTIONS = 5
_REFUSED_FOR_RIGHTS = frozenset({"NOT_AUTHORIZED", "CAPABILITY_UNAVAILABLE", "ACCOUNT_INELIGIBLE"})


def _smuggles_handle(value: Any) -> bool:
    if isinstance(value, str):
        return _HANDLE.fullmatch(value) is not None
    if isinstance(value, Mapping):
        return any(_smuggles_handle(v) for v in value.values())
    if isinstance(value, list):
        return any(_smuggles_handle(v) for v in value)
    return False


def _action(tool: str, why: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"tool": tool, "why": why, "arguments": dict(arguments)}


def next_actions(
    spec: ToolSpec, arguments: Mapping[str, Any], result: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Follow-ups a caller may take: the next page, why a write was refused, how to settle an
    unknown outcome, the invite a direct add needs. Suggestions only; nothing runs."""
    actions = []
    base = {k: v for k, v in arguments.items() if k not in ("request_id", "cursor")}
    cursor = result.get("next_cursor")
    if isinstance(cursor, str):
        if "cursor" in spec.input_schema.get("properties", {}):
            actions.append(_action(spec.name, "more results", {**base, "cursor": cursor}))
        elif cursor.startswith("cur_"):
            actions.append(_action("comms_context_page", "more results", {"cursor": cursor}))
    group = arguments.get("group")
    outcome, code = result.get("result"), result.get("code")
    if isinstance(group, str):
        if outcome == "FAILED" and code in _REFUSED_FOR_RIGHTS:
            actions.append(
                _action("comms_capability_for_group", "see who may do this", {"group": group})
            )
        if outcome == "OUTCOME_UNKNOWN":
            actions.append(
                _action("comms_context_recent", "check whether it happened", {"group": group})
            )
        if outcome == "INVITE_REQUIRED" and isinstance(arguments.get("recipient"), str):
            actions.append(
                _action(
                    "comms_group_member_invite",
                    "the provider needs an invite",
                    {"group": group, "recipient": arguments["recipient"]},
                )
            )
    return actions[:_MAX_ACTIONS]


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
        if not spec.read_only and _smuggles_handle(dict(arguments)):
            return ToolResult(error_code="INVALID_ARGUMENT")  # A32: context is never authority
        try:
            result = self._services.call(spec.service, client=client, arguments=dict(arguments))
        except CommsError as refused:
            return ToolResult(error_code=refused.code)
        if not isinstance(result, Mapping) or not self._outputs[name].is_valid(dict(result)):
            return ToolResult(error_code="INTERNAL_ERROR")  # fail closed: never an unchecked shape
        return ToolResult(
            structured={**result, "next_actions": next_actions(spec, arguments, result)}
        )
