"""The service error model (comms v0.3 Task D2; P §54).

``CommsError`` carries one code from P §54 or the four named additions, with one fixed message
per code: nothing a caller or a provider supplied is ever in the text (P §54: never expose
credentials or raw provider identity). The only structured detail is ``retry_after`` on
``RATE_LIMITED``.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["ERROR_CODES", "MESSAGES", "NAMED_ADDITIONS", "CommsError"]

MESSAGES = MappingProxyType(
    {
        # P §54
        "NOT_FOUND": "the target was not found",
        "AMBIGUOUS_TARGET": "more than one target matches",
        "INVALID_ARGUMENT": "an argument is invalid",
        "CAPABILITY_UNAVAILABLE": "the capability is not available here",
        "NOT_AUTHORIZED": "the provider refused: not authorized",
        "ACCOUNT_INELIGIBLE": "the account is not eligible for this operation",
        "PROVIDER_UNSUPPORTED": "the provider does not support this operation",
        "NOT_CONFIGURED": "the provider is not configured",
        "RATE_LIMITED": "the provider is rate limiting; retry later",
        "PROVIDER_UNAVAILABLE": "the provider is unavailable",
        "TEMPLATE_REQUIRED": "a template is required outside the messaging window",
        "TEMPLATE_UNAVAILABLE": "the template is not available",
        "OUTCOME_UNKNOWN": "the outcome is unknown; resolve before retrying",
        "POLICY_CHANGED": "the policy changed since this was prepared",
        "STALE_HANDLE": "the handle is stale",
        "INTERNAL_ERROR": "an internal error occurred",
        # named additions (design D.2, D.5)
        "REQUEST_ID_REUSE": "the request id was used for a different request",
        "RETIRED_TOOL": "the tool is retired",
        "TOOL_NOT_FOUND": "no such tool",
        "AUDIT_INTEGRITY_DEGRADED": "the audit trail is degraded; no new effect may start",
    }
)
NAMED_ADDITIONS = frozenset(
    {"REQUEST_ID_REUSE", "RETIRED_TOOL", "TOOL_NOT_FOUND", "AUDIT_INTEGRITY_DEGRADED"}
)
ERROR_CODES = frozenset(MESSAGES)


class CommsError(Exception):
    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        if not isinstance(code, str) or code not in MESSAGES:
            raise ValueError("unknown error code")
        if retry_after is not None and code != "RATE_LIMITED":
            raise ValueError("only a rate limit carries a wait")
        super().__init__(MESSAGES[code])
        self.code = code
        self.retry_after = retry_after
