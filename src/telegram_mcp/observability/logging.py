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


class _SdkHeaderRedactionFilter(logging.Filter):
    """Replace SDK transport-security warnings that echo Host/Origin values."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken log record must never leak; use fixed text
            record.msg = "transport security warning"
            record.args = ()
            return True
        if message.startswith("Invalid Host header:"):
            record.msg = "Invalid Host header"
            record.args = ()
        elif message.startswith("Invalid Origin header:"):
            record.msg = "Invalid Origin header"
            record.args = ()
        return True


def install_safe_logging() -> None:
    """Application log configuration: allowlisted app events, quiet SDK/HTTP."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logger.setLevel(logging.INFO)
    sdk_logger = logging.getLogger("mcp.server.transport_security")
    if not any(isinstance(f, _SdkHeaderRedactionFilter) for f in sdk_logger.filters):
        sdk_logger.addFilter(_SdkHeaderRedactionFilter())
    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
