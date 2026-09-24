"""The authenticated loopback coding-client ingress (spec §8.2.1; design §2).

Layering, outermost first: bearer gate (401 before the body is read) ->
no-store -> strict preflight with rate admission (400 / 429) -> the SDK's
Streamable HTTP app with DNS-rebinding protection (§29.3) -> ``on_call_tool``.
The principal reaches the handler through ``PRINCIPAL``; it is identity only.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings

from comms.transports.telegram.contract import EXPECTED_TOOLS, load_contracts
from comms.transports.telegram.http_guards import (
    RateLimiter,
    bearer_gate,
    duplicate_key_preflight,
    no_store,
)
from comms.transports.telegram.results import error_result, success_result, unknown_tool_result
from comms.transports.telegram.runtime.identity import PrincipalContext
from comms.transports.telegram.sensitive_dispatch import SensitiveDispatcher
from comms.transports.telegram.server import BASE_INSTRUCTIONS, _descriptors
from comms.transports.telegram.tools.status import make_status
from comms.transports.telegram.validation import ArgumentError, validate_arguments

__all__ = ["PRINCIPAL", "create_ingress_app"]

PRINCIPAL: ContextVar[PrincipalContext | None] = ContextVar("telegram_mcp_principal", default=None)

INGRESS_INSTRUCTIONS = BASE_INSTRUCTIONS + (
    " This build serves the project catalogue; other reads are unavailable."
)


def _server(
    dispatcher: SensitiveDispatcher, status_key: tuple[str, str], max_response_bytes: int
) -> Server:
    tools = _descriptors()

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: Any) -> types.CallToolResult:
        arguments = {} if "arguments" not in params.model_fields_set else params.arguments
        name = params.name
        if name not in EXPECTED_TOOLS:
            return unknown_tool_result()
        try:
            validated = validate_arguments(load_contracts()[name], arguments)
        except ArgumentError as exc:
            return error_result(exc.code)
        if name == "telegram_status":
            status = make_status(disclosure_key=status_key)
            return success_result(
                name, status["data"], status["meta"], max_response_bytes=max_response_bytes
            )
        principal = PRINCIPAL.get()
        if principal is None:  # unreachable behind the gate; fail closed anyway
            return error_result("AUTH_REQUIRED")
        return await dispatcher.call(name, validated, principal)

    server = Server(
        "telegram-mcp",
        instructions=INGRESS_INSTRUCTIONS,
        version="0.1.10",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    server.middleware = []
    return server


def create_ingress_app(
    *,
    host: str,
    port: int,
    authenticate: Callable[[str], PrincipalContext | None],
    dispatcher: SensitiveDispatcher,
    limiter: RateLimiter,
    status_key: tuple[str, str],
    max_request_bytes: int = 65536,
    max_response_bytes: int = 65536,
) -> Any:
    sdk_app = _server(dispatcher, status_key, max_response_bytes).streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=max_request_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:{port}"],
            allowed_origins=[f"http://{host}:{port}"],
        ),
        host=host,
        debug=False,
    )

    def admit(body: Any) -> float | None:
        if not isinstance(body, dict) or body.get("method") != "tools/call":
            return None
        params = body.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        principal = PRINCIPAL.get()
        if not isinstance(name, str) or principal is None:
            return None
        return limiter.check(principal.client_ref, name)

    guarded = duplicate_key_preflight(no_store(sdk_app), max_request_bytes, admit=admit)
    return bearer_gate(guarded, authenticate=authenticate, principal_var=PRINCIPAL)
