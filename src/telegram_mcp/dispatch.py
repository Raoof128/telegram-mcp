"""Name allowlist, validation, status routing and fail-closed refusal."""

import asyncio

import mcp.types as types

from telegram_mcp.contract import EXPECTED_TOOLS, load_contracts
from telegram_mcp.results import error_result, success_result, unknown_tool_result
from telegram_mcp.tools.status import make_status
from telegram_mcp.validation import ArgumentError, validate_arguments


def dispatch(
    name: str, arguments: object, *, max_response_bytes: int = 65536
) -> types.CallToolResult:
    if name not in EXPECTED_TOOLS:
        return unknown_tool_result()
    contracts = load_contracts()
    try:
        validated = validate_arguments(contracts[name], arguments)
    except ArgumentError as exc:
        return error_result(exc.code)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        return error_result("INTERNAL_ERROR")
    try:
        if name == "telegram_status":
            status = make_status()
            return success_result(
                name, status["data"], status["meta"], max_response_bytes=max_response_bytes
            )
        return error_result("POLICY_UNCONFIGURED")
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        return error_result("INTERNAL_ERROR")
