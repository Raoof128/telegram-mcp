"""SDK result construction and privacy-safe bounded errors."""

from mcp import types

from comms.transports.telegram.contract import load_contracts
from comms.transports.telegram.observability.logging import emit_event

_FIXED_MESSAGE = "The request could not be completed."
_TOOL_NOT_FOUND_MESSAGE = "Unknown tool (TOOL_NOT_FOUND)."


def _error_codes() -> set[str]:
    codes: set[str] = set()
    for contracts in (load_contracts(),):
        for contract in contracts.values():
            schema = contract.output_schema
            codes.update(schema["oneOf"][1]["properties"]["error"]["properties"]["code"]["enum"])
    return codes


# Spec §27.1, the Retryable column, verbatim: the one registry. "yes" and "no"
# are fixed; a caller's retryable only resolves a "maybe".
RETRYABILITY: dict[str, str] = {
    "AUTH_REQUIRED": "no",
    "SESSION_REVOKED": "no",
    "ACCOUNT_UNAVAILABLE": "maybe",
    "POLICY_UNCONFIGURED": "no",
    "REF_NOT_FOUND": "no",
    "NOT_ACCESSIBLE": "no",
    "MESSAGE_NOT_FOUND": "no",
    "AMBIGUOUS_PEER": "no",
    "INVALID_CURSOR": "no",
    "CURSOR_EXPIRED": "yes",
    "CURSOR_POLICY_CHANGED": "yes",
    "CURSOR_PROJECT_CHANGED": "yes",
    "INVALID_TIME": "no",
    "INVALID_ARGUMENT": "no",
    "RESPONSE_LIMIT": "yes",
    "EXPOSURE_BUDGET_EXCEEDED": "maybe",
    "SECURITY_LOCKED": "no",
    "PROOF_GENERATION_FAILED": "maybe",
    "AUDIT_INTEGRITY_UNAVAILABLE": "no",
    "WORK_BUDGET_EXCEEDED": "yes",
    "FLOOD_WAIT": "yes",
    "TELEGRAM_UNAVAILABLE": "yes",
    "CLIENT_REVOKED": "no",
    "CONSENT_DENIED": "no",
    "CONSENT_UNAVAILABLE": "no",
    "POLICY_CHANGED": "yes",
    "DEADLINE_EXCEEDED": "yes",
    "UNSUPPORTED_RELEASE_PROFILE": "no",
    "INTERNAL_ERROR": "maybe",
}


def error_result(
    code: str, *, retryable: bool | None = None, retry_after_seconds: int | None = None
) -> types.CallToolResult:
    if code not in _error_codes():
        raise ValueError("unknown error code")
    if retry_after_seconds is not None and retry_after_seconds < 0:
        raise ValueError("retry_after_seconds must be non-negative")
    row = RETRYABILITY[code]
    body = {
        "ok": False,
        "error": {
            "code": code,
            "message": _FIXED_MESSAGE,
            "retryable": row == "yes" or (row == "maybe" and bool(retryable)),
            "retry_after_seconds": retry_after_seconds,
        },
    }
    emit_event("error", code)
    return types.CallToolResult(
        structured_content=body,
        content=[types.TextContent(type="text", text=_FIXED_MESSAGE)],
        is_error=True,
    )


def unknown_tool_result() -> types.CallToolResult:
    emit_event("error", "TOOL_NOT_FOUND")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_TOOL_NOT_FOUND_MESSAGE)],
        is_error=True,
    )


# Measured SDK/JSON-RPC envelope overhead is ~79 B; reserve 512 B so the
# complete wire body can never exceed the configured cap after SDK metadata.
_ENVELOPE_OVERHEAD_RESERVE = 512


def success_result(
    tool: str, data: dict, meta: dict, *, max_response_bytes: int = 65536
) -> types.CallToolResult:
    from comms.transports.telegram.contract import validate_output

    body = {"ok": True, "data": data, "meta": meta}
    validate_output(tool, body)
    result = types.CallToolResult(
        structured_content=body,
        content=[types.TextContent(type="text", text=f"{tool} completed.")],
        is_error=False,
    )
    wire = result.model_dump_json(by_alias=True).encode("utf-8")
    if len(wire) + _ENVELOPE_OVERHEAD_RESERVE > max_response_bytes:
        return error_result("RESPONSE_LIMIT")
    emit_event("ok", None)
    return result
