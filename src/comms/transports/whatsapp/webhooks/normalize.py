"""A Meta webhook body, reduced to what the fan-out acts on (comms v0.3 Task C29; C.5).

Inbound messages give the window fact (who wrote, when, which message); statuses give provider
updates. Text never leaves this module: the archive receives the raw body itself. A body that
is not the documented shape raises ``ValueError``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from comms.core import timeutil

__all__ = ["Inbound", "StatusReport", "parse"]

# Meta status → the core's provider statuses (a read message was delivered).
STATUS_MAP = {
    "sent": "ACCEPTED",
    "delivered": "DELIVERED",
    "read": "DELIVERED",
    "failed": "FAILED_PERMANENT",
}


@dataclass(frozen=True)
class Inbound:
    wamid: str
    from_number: str
    at: str


@dataclass(frozen=True)
class StatusReport:
    event_ref: str
    wamid: str
    status: str


def _at(timestamp: object) -> str:
    if not isinstance(timestamp, str) or not timestamp.isdigit():
        raise ValueError("webhook timestamp refused")
    return timeutil.iso(datetime.fromtimestamp(int(timestamp), UTC))


def parse(raw: bytes) -> tuple[list[Inbound], list[StatusReport]]:
    try:
        payload = json.loads(raw)
        entries = payload["entry"]
    except (ValueError, KeyError, TypeError):
        raise ValueError("webhook body refused") from None
    inbound: list[Inbound] = []
    statuses: list[StatusReport] = []
    for entry in entries if isinstance(entries, list) else ():
        for change in entry.get("changes") or () if isinstance(entry, dict) else ():
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                continue
            for message in value.get("messages") or ():
                if (
                    isinstance(message, dict)
                    and isinstance(message.get("id"), str)
                    and isinstance(message.get("from"), str)
                ):
                    inbound.append(
                        Inbound(message["id"], "+" + message["from"], _at(message.get("timestamp")))
                    )
            for status in value.get("statuses") or ():
                mapped = (
                    STATUS_MAP.get(str(status.get("status"))) if isinstance(status, dict) else None
                )
                if mapped is None or not isinstance(status.get("id"), str):
                    continue
                ref = f"{status['id']}:{status['status']}:{status.get('timestamp')}"
                statuses.append(StatusReport(ref, status["id"], mapped))
    return inbound, statuses
