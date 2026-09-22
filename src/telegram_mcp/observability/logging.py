"""Fixed allowlisted application events; never logs objects, headers or exceptions."""

import logging

logger = logging.getLogger("telegram_mcp")

_ALLOWED_STATUSES = {"ok", "error", "refused"}
_ALLOWED_CODES = {
    "AUTH_REQUIRED",
    "SESSION_REVOKED",
    "ACCOUNT_UNAVAILABLE",
    "POLICY_UNCONFIGURED",
    "REF_NOT_FOUND",
    "NOT_ACCESSIBLE",
    "MESSAGE_NOT_FOUND",
    "AMBIGUOUS_PEER",
    "INVALID_CURSOR",
    "CURSOR_EXPIRED",
    "CURSOR_POLICY_CHANGED",
    "CURSOR_PROJECT_CHANGED",
    "INVALID_TIME",
    "INVALID_ARGUMENT",
    "RESPONSE_LIMIT",
    "EXPOSURE_BUDGET_EXCEEDED",
    "SECURITY_LOCKED",
    "PROOF_GENERATION_FAILED",
    "AUDIT_INTEGRITY_UNAVAILABLE",
    "WORK_BUDGET_EXCEEDED",
    "FLOOD_WAIT",
    "TELEGRAM_UNAVAILABLE",
    "CLIENT_REVOKED",
    "CONSENT_DENIED",
    "CONSENT_UNAVAILABLE",
    "POLICY_CHANGED",
    "DEADLINE_EXCEEDED",
    "UNSUPPORTED_RELEASE_PROFILE",
    "INTERNAL_ERROR",
    "TOOL_NOT_FOUND",
}


def emit_event(status: str, error_code: str | None) -> None:
    if status not in _ALLOWED_STATUSES:
        status = "error"
    if error_code is not None and error_code not in _ALLOWED_CODES:
        error_code = "INTERNAL_ERROR"
    logger.info("event status=%s error_code=%s", status, error_code)
