"""SDK result construction and privacy-safe bounded errors."""

import mcp.types as types

from telegram_mcp.contract import load_contracts
from telegram_mcp.observability.logging import emit_event

_FIXED_MESSAGE = "The request could not be completed."
_TOOL_NOT_FOUND_MESSAGE = "Unknown tool (TOOL_NOT_FOUND)."


def _error_codes() -> set[str]:
    contracts = load_contracts()
    any_tool = next(iter(contracts))
    schema = contracts[any_tool].output_schema
    return set(schema["oneOf"][1]["properties"]["error"]["properties"]["code"]["enum"])


def error_result(code: str) -> types.CallToolResult:
    if code not in _error_codes():
        raise ValueError("unknown error code")
    body = {
        "ok": False,
        "error": {
            "code": code,
            "message": _FIXED_MESSAGE,
            "retryable": False,
            "retry_after_seconds": None,
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


def success_result(
    tool: str, data: dict, meta: dict, *, max_response_bytes: int = 65536
) -> types.CallToolResult:
    from telegram_mcp.contract import validate_output

    body = {"ok": True, "data": data, "meta": meta}
    validate_output(tool, body)
    result = types.CallToolResult(
        structured_content=body,
        content=[types.TextContent(type="text", text=f"{tool} completed.")],
        is_error=False,
    )
    wire = result.model_dump_json(by_alias=True).encode("utf-8")
    if len(wire) > max_response_bytes:
        return error_result("RESPONSE_LIMIT")
    emit_event("ok", None)
    return result
