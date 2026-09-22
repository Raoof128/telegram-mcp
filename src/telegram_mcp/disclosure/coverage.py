"""Search coverage proof (frozen spec §23D; design §3.3).

The coverage object is already frozen in ``contracts/meta.json``. What lives
here is everything JSON Schema cannot express: the implication chain between
``complete``, ``next_cursor``, ``partial`` and ``partial_reasons``, and the
digest that binds the emitted object into the signed proof.

**Deduplication runs the opposite way from exposure accounting.** A shared
canonical peer is counted once in the global totals and once in each
contributing project's local counts; exposure accounting deliberately
over-counts the same record in every contributing project bucket. Copying
either rule onto the other is the easiest mistake in Phase 3.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "PARTIAL_REASONS",
    "CoverageError",
    "build_coverage",
    "coverage_digest",
    "validate_coverage",
]

# Closed list, spec §23D. Not extensible without a contract change.
PARTIAL_REASONS: tuple[str, ...] = (
    "deadline",
    "rpc_budget",
    "hit_budget",
    "peer_budget",
    "response_limit",
    "telegram_partial",
)

_MAX_PROJECTS = 8


class CoverageError(Exception):
    """The coverage object is internally inconsistent or out of contract."""


def build_coverage(
    *,
    complete: bool,
    eligible_peers: int,
    peers_scanned: int,
    telegram_rpcs: int,
    hits_examined: int,
    hits_returned: int,
    partial_reasons: Sequence[str],
    project_coverage: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble a contract-shaped coverage object, rejecting bad input."""
    unknown = [r for r in partial_reasons if r not in PARTIAL_REASONS]
    if unknown:
        raise CoverageError("unknown partial reason")
    if len(set(partial_reasons)) != len(partial_reasons):
        raise CoverageError("duplicate partial reason")
    if not 1 <= len(project_coverage) <= _MAX_PROJECTS:
        raise CoverageError("project_coverage must hold 1..8 entries")
    for count in (eligible_peers, peers_scanned, telegram_rpcs, hits_examined, hits_returned):
        if count < 0:
            raise CoverageError("coverage counts must be non-negative")
    if peers_scanned > eligible_peers:
        raise CoverageError("peers_scanned exceeds eligible_peers")

    return {
        "complete": bool(complete),
        "eligible_peers": eligible_peers,
        "peers_scanned": peers_scanned,
        "telegram_rpcs": telegram_rpcs,
        "hits_examined": hits_examined,
        "hits_returned": hits_returned,
        "partial_reasons": list(partial_reasons),
        "project_coverage": [dict(entry) for entry in project_coverage],
    }


def validate_coverage(
    coverage: Mapping[str, Any], *, next_cursor: str | None, partial: bool
) -> None:
    """Enforce the §23D implication chain. Raises, never warns."""
    complete = bool(coverage["complete"])
    reasons = list(coverage["partial_reasons"])

    if complete:
        if next_cursor is not None:
            raise CoverageError("complete coverage cannot carry a next_cursor")
        if partial:
            raise CoverageError("complete coverage cannot be partial")
        if reasons:
            raise CoverageError("complete coverage cannot carry partial reasons")
        if coverage["peers_scanned"] != coverage["eligible_peers"]:
            raise CoverageError("complete coverage must have scanned every eligible peer")
        return

    if next_cursor is not None and "response_limit" not in reasons:
        raise CoverageError("a next_cursor requires response_limit in partial_reasons")


def coverage_digest(coverage: Mapping[str, Any] | None) -> str | None:
    """``SHA-256(JCS(coverage))`` in lowercase hex; ``None`` for non-search tools."""
    if coverage is None:
        return None
    return hashlib.sha256(jcs_dumps(coverage)).hexdigest()
