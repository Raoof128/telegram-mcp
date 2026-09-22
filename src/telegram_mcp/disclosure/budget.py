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
from dataclasses import dataclass
from datetime import UTC, datetime

from telegram_mcp.keys.store import load_key

__all__ = [
    "GLOBAL",
    "PROJECT",
    "BucketKey",
    "BudgetError",
    "Usage",
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
