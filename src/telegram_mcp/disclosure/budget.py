"""Cumulative exposure budgets (frozen spec §23C; design §5).

Two dimensions, not two buckets: ``client_global``, and ``(client, origin
project)`` for every contributing project. A cross-project search over three
projects therefore touches four physical buckets.

Committed usage is recomputed from ``exposure_ledger`` on every consultation.
Nothing is cached: a cached ceiling is a bypassable ceiling.

All record and byte quantities come from ``telegram_mcp.disclosure.measure``.
Computing one here would give the prompt and the ledger two different truths,
which is exactly what Gate P forbids.
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from telegram_mcp.disclosure.measure import (
    RECORD_ELEMENT,
    bytes_disclosed,
    project_bytes,
    records_disclosed,
)
from telegram_mcp.keys.store import load_key

__all__ = [
    "GLOBAL",
    "PROJECT",
    "BucketKey",
    "BudgetError",
    "Usage",
    "buckets_for",
    "committed_usage",
    "subject_digest",
    "window_start",
]

# Mirrors the CHECK on exposure_ledger.budget_subject_kind (spec §12.2).
GLOBAL = "client_global"
PROJECT = "project"
_KINDS = (GLOBAL, PROJECT)

_SUBJECT_DOMAIN = b"telegram-mcp-budget-subject-v1"


class BudgetError(Exception):
    """A budget could not be computed, or a ceiling was reached."""


@dataclass(frozen=True)
class BucketKey:
    """One accounting bucket: a client in one of the two dimensions."""

    client_id: int
    kind: str
    subject_digest: str


@dataclass(frozen=True)
class Usage:
    """Records and bytes, the two quantities every ceiling is expressed in."""

    records: int
    bytes: int

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.records + other.records, self.bytes + other.bytes)


def subject_digest(kind: str, subject: str = "") -> str:
    """Keyed, deterministic digest so no opaque ref lands in the ledger."""
    if kind not in _KINDS:
        raise ValueError("unknown budget subject kind")
    if kind == GLOBAL and subject:
        raise ValueError("client_global takes no subject")
    if kind == PROJECT and not subject:
        raise ValueError("project subject is required")
    key = load_key("privacy-key")
    message = _SUBJECT_DOMAIN + kind.encode("ascii") + subject.encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def window_start(now: float, minutes: int) -> str:
    """Start of the rolling window, as the ISO-8601 ``Z`` form the ledger uses."""
    if minutes <= 0:
        raise ValueError("rolling window must be positive")
    started = datetime.fromtimestamp(now - minutes * 60, tz=UTC)
    return started.strftime("%Y-%m-%dT%H:%M:%SZ")


def committed_usage(conn: sqlite3.Connection, key: BucketKey, *, since: str) -> Usage:
    """Sum the ledger rows for one bucket inside the rolling window."""
    row = conn.execute(
        "SELECT COALESCE(SUM(records_disclosed), 0), COALESCE(SUM(bytes_disclosed), 0)"
        " FROM exposure_ledger"
        " WHERE client_id = ? AND budget_subject_kind = ?"
        "   AND budget_subject_digest = ? AND ts >= ?",
        (key.client_id, key.kind, key.subject_digest, since),
    ).fetchone()
    return Usage(int(row[0]), int(row[1]))


def buckets_for(
    tool_name: str, data: Mapping[str, Any], *, client_id: int
) -> dict[BucketKey, Usage]:
    """Every bucket this response charges, with the quantity for each.

    The global bucket takes the whole ``data`` object's canonical bytes and
    every record. Each contributing project takes its own records and the
    canonical bytes of the records attributed to it — ``data``-container
    overhead is global only, and ``meta`` (including ``coverage`` and the
    proof envelope) is outside the measurement entirely.
    """
    global_key = BucketKey(client_id, GLOBAL, subject_digest(GLOBAL))
    buckets: dict[BucketKey, Usage] = {
        global_key: Usage(records_disclosed(tool_name, data), bytes_disclosed(data))
    }

    per_project_bytes = project_bytes(tool_name, data)
    per_project_records: dict[str, int] = {}
    for record in data.get(RECORD_ELEMENT[tool_name], []):
        for project_ref in record.get("origin_project_refs") or []:
            per_project_records[project_ref] = per_project_records.get(project_ref, 0) + 1

    for project_ref, size in per_project_bytes.items():
        key = BucketKey(client_id, PROJECT, subject_digest(PROJECT, project_ref))
        buckets[key] = Usage(per_project_records.get(project_ref, 0), size)

    return buckets
