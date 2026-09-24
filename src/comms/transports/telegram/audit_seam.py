"""Audit emission seam: allowlisted events only, no chain.

``AuditEvent`` mirrors Appendix C of the V0.1.10 engineering spec field
for field: refs/epochs/counts/status only. No search term, message body,
username, phone number, raw Telegram ID, bearer token, or query
fingerprint can be stored — the frozen dataclass has no such fields, so
``emit_audit`` cannot leak them.

``emit_audit`` logs the allowlisted field mapping at INFO on the
``telegram_mcp.audit`` logger and appends the event to the in-memory
sink visible to tests via ``get_audit_sink()``. Chain, checkpoints,
receipts, and ledgers are Phase 3 and must not appear here.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

__all__ = [
    "AuditEvent",
    "emit_audit",
    "get_audit_sink",
]

_logger = logging.getLogger("telegram_mcp.audit")

# In-memory sink for tests; production persistence lands in Phase 3.
_AUDIT_SINK: list[AuditEvent] = []


@dataclass(frozen=True)
class AuditEvent:
    """Safe audit event: Appendix C fields exactly, nothing else."""

    event_id: str
    ts: str
    tool_name: str
    principal_ref: str
    client_ref: str
    account_ref: str
    peer_ref: str | None
    policy_epoch: int
    result_count: int
    duration_ms: int
    telegram_rpc_count: int
    status: str
    error_code: str | None


def get_audit_sink() -> list[AuditEvent]:
    """Live in-memory audit sink (tests clear and inspect it)."""
    return _AUDIT_SINK


def emit_audit(event: AuditEvent) -> None:
    """Log allowlisted fields only; append the event to the test sink."""
    if not isinstance(event, AuditEvent):
        raise ValueError("invalid audit event")  # noqa: TRY004 -- uniform ValueError on seam validation
    _logger.info("audit_event", extra={"audit": dataclasses.asdict(event)})
    _AUDIT_SINK.append(event)
