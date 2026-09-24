"""The legacy Telegram chain profile for the core engine (comms v0.3 R-002).

Telegram-specific: the 16 §12.2 event columns, the closed tool_name vocabulary,
``evt_`` event IDs and ``tgl_`` checkpoint refs. Byte-identical to the pre-v0.3
chain (tests/fixtures/audit/legacy_chain_vectors.json).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.audit.chain import ChainError, ChainProfile
from comms.core.opaque import mint_opaque_ref

__all__ = [
    "ADMIN_EVENTS",
    "ALLOWED_TOOL_NAMES",
    "CHECKPOINT_DOMAIN",
    "EVENT_COLUMNS",
    "EVENT_DOMAIN",
    "GENESIS_DOMAIN",
    "LEGACY_TELEGRAM",
]

# The frozen legacy wire domains: defined once, here (comms v0.3 Task A5).
EVENT_DOMAIN = b"telegram-mcp-audit-v1"
GENESIS_DOMAIN = b"telegram-mcp-audit-genesis-v1"
CHECKPOINT_DOMAIN = b"telegram-mcp-checkpoint-v1"

EVENT_COLUMNS = (
    "event_id",
    "ts",
    "tool_name",
    "principal_ref",
    "client_ref",
    "account_ref",
    "peer_ref",
    "project_ref",
    "project_count",
    "policy_epoch",
    "result_count",
    "duration_ms",
    "telegram_rpc_count",
    "status",
    "error_code",
    "disclosure_ref",
)
ADMIN_EVENTS: tuple[str, ...] = (
    "admin.lock",
    "admin.unlock",
    "admin.key_rotation",
    "admin.repair_anchor",
    "admin.policy_import",
)
_TOOLS = (
    "telegram_status",
    "telegram_list_projects",
    "telegram_resolve_project",
    "telegram_list_chats",
    "telegram_resolve_peer",
    "telegram_get_unread",
    "telegram_get_messages",
    "telegram_get_context",
    "telegram_search_messages",
    "telegram_cross_project_search",
)
ALLOWED_TOOL_NAMES = frozenset(_TOOLS) | frozenset(ADMIN_EVENTS)


def _validate(event: Mapping[str, Any]) -> None:
    if event.get("tool_name") not in ALLOWED_TOOL_NAMES:
        raise ChainError("event tool_name is not in the closed vocabulary")


LEGACY_TELEGRAM = ChainProfile(
    name="legacy_telegram",
    event_domain=EVENT_DOMAIN,
    genesis_domain=GENESIS_DOMAIN,
    checkpoint_domain=CHECKPOINT_DOMAIN,
    event_columns=EVENT_COLUMNS,
    validate_event=_validate,
    mint_checkpoint_ref=lambda: mint_opaque_ref("tgl_"),
)
