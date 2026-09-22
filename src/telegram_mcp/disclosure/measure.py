"""The single measurement authority (design §5.1; frozen spec §23C.2).

Gate P requires that exposure measurement be identical between prompts,
reservations, ledger rows and receipts. Those are four call sites, so the
arithmetic lives here once and is imported. A second copy is the defect.

Every function takes the ``data`` object *after* egress transformation and
*before* the ``meta``/proof envelope, which is what §23C.2 measures.
``meta`` — including ``coverage`` and the proof envelope — is outside the
measurement entirely.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "RECORD_ELEMENT",
    "MeasurementError",
    "bytes_disclosed",
    "container_bytes",
    "project_bytes",
    "records_disclosed",
]

# Read off the frozen contracts in src/telegram_mcp/contracts/*.data.json,
# not inferred from tool names: the resolve tools emit ``matches``, unread
# emits ``chats``, and cross-project search carries a ``projects`` array
# that is scope metadata rather than records.
RECORD_ELEMENT: dict[str, str] = {
    "telegram_list_projects": "projects",
    "telegram_resolve_project": "matches",
    "telegram_list_chats": "chats",
    "telegram_resolve_peer": "matches",
    "telegram_get_unread": "chats",
    "telegram_get_messages": "messages",
    "telegram_get_context": "messages",
    "telegram_search_messages": "results",
    "telegram_cross_project_search": "results",
}


class MeasurementError(Exception):
    """Measurement was requested for something that is not accounted."""


def _element(tool_name: str) -> str:
    key = RECORD_ELEMENT.get(tool_name)
    if key is None:
        # telegram_status is the sole non-sensitive tool and is never
        # exposure-accounted (spec §23C.2).
        raise MeasurementError("tool is not exposure-accounted")
    return key


def _records(tool_name: str, data: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    records = data.get(_element(tool_name), [])
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise MeasurementError("record element is not a sequence")
    return records


def records_disclosed(tool_name: str, data: Mapping[str, Any]) -> int:
    """Count the records actually present in ``data`` for this tool."""
    return len(_records(tool_name, data))


def bytes_disclosed(data: Mapping[str, Any]) -> int:
    """UTF-8 length of the canonical JSON encoding of ``data``."""
    return len(jcs_dumps(data))


def project_bytes(tool_name: str, data: Mapping[str, Any]) -> dict[str, int]:
    """Canonical bytes attributed to each contributing project.

    A record with several origins is counted **whole** in every contributing
    bucket, so the sum of project bytes may exceed ``bytes_disclosed``. That
    over-count is deliberate: it is what stops an attacker cycling projects
    to dilute the per-project ceiling.
    """
    totals: dict[str, int] = {}
    for record in _records(tool_name, data):
        size = len(jcs_dumps(record))
        origins = record.get("origin_project_refs") or []
        for project_ref in origins:
            totals[project_ref] = totals.get(project_ref, 0) + size
    return totals


def container_bytes(tool_name: str, data: Mapping[str, Any]) -> int:
    """``data`` bytes that are not records: charged to the global bucket only.

    For a single-origin response this reconciles exactly:
    ``sum(project_bytes) + container_bytes == bytes_disclosed``.
    """
    records_size = sum(len(jcs_dumps(r)) for r in _records(tool_name, data))
    return bytes_disclosed(data) - records_size
