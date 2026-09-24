"""Metadata-effective access (Phase-5 design §2.3). Pure: no storage, no Telegram.

A row is what the one evaluator decides for one client, project and member
under the stored metadata. Rows carry opaque refs only; canonical
``type:id`` identities stay inside this function.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from telegram_mcp.authority.policy import (
    AuthorityRequest,
    AuthorityView,
    Denial,
    PeerFacts,
    TraceStep,
    evaluate_with_trace,
)
from telegram_mcp.consent.challenge import jcs_dumps

__all__ = ["AccessRow", "diff_rows", "effective_rows", "rows_digest", "trace_digest"]


def trace_digest(trace: Iterable[TraceStep]) -> str:
    return hashlib.sha256(jcs_dumps([list(step) for step in trace])).hexdigest()


@dataclass(frozen=True)
class AccessRow:
    client_ref: str
    project_ref: str
    peer_ref: str  # "" for the project-level row
    operation: str  # "discover" | "read" | "cross_search"
    decision: str  # "allow" or a denial code
    egress: str  # "" when denied
    excerpt_max: int | None
    facts: str  # "n/a" | "exact" | "unknown"
    owner_class: str  # "n/a" | "pass" | "unknown" | "deny": allow with "unknown" is conditional
    trace_digest: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.client_ref, self.project_ref, self.peer_ref, self.operation)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _row(
    view: AuthorityView,
    request: AuthorityRequest,
    *,
    peer_ref: str,
    facts: str,
) -> AccessRow:
    verdict, trace = evaluate_with_trace(view, request)
    outcome = dict(trace).get("owner_class")
    owner_class = {
        None: "n/a",
        "pass": "pass",
        "facts_unknown": "unknown",
    }.get(outcome, "deny")
    if isinstance(verdict, Denial):
        decision, egress, excerpt = verdict.code, "", None
    else:
        decision = "allow"
        egress = verdict.effective_egress.level
        excerpt = verdict.effective_egress.excerpt_limit
    return AccessRow(
        client_ref=request.client_ref,
        project_ref=request.project_refs[0],
        peer_ref=peer_ref,
        operation=request.operation,
        decision=decision,
        egress=egress,
        excerpt_max=excerpt,
        facts=facts,
        owner_class=owner_class,
        trace_digest=trace_digest(trace),
    )


def effective_rows(
    view: AuthorityView, peers: Mapping[str, tuple[str, PeerFacts]]
) -> tuple[AccessRow, ...]:
    """Every client x project, plus every member for read and cross_search."""
    rows: list[AccessRow] = []
    for client_ref in sorted(view.clients):
        for project_ref in sorted(view.projects):
            rows.append(
                _row(
                    view,
                    AuthorityRequest("discover", client_ref, (project_ref,)),
                    peer_ref="",
                    facts="n/a",
                )
            )
            for identity in sorted(view.memberships.get(project_ref, frozenset())):
                peer_ref, facts = peers[identity]
                exact = facts.chat_type is not None and facts.archived is not None
                for operation in ("read", "cross_search"):
                    rows.append(
                        _row(
                            view,
                            AuthorityRequest(
                                operation, client_ref, (project_ref,), identity, facts=facts
                            ),
                            peer_ref=peer_ref,
                            facts="exact" if exact else "unknown",
                        )
                    )
    return tuple(sorted(rows, key=lambda r: r.key))


def rows_digest(rows: Iterable[AccessRow]) -> str:
    return hashlib.sha256(jcs_dumps([r.to_json() for r in rows])).hexdigest()


# owner_class is compared, so a class/archive switch that turns "unknown" into
# "pass" is a visible change even when decision stays "allow" (review #8).
_COMPARED = ("decision", "egress", "excerpt_max", "facts", "owner_class")


def diff_rows(
    before: Iterable[AccessRow], after: Iterable[AccessRow]
) -> dict[str, list[dict[str, Any]]]:
    old = {r.key: r for r in before}
    new = {r.key: r for r in after}
    changed = []
    for key in sorted(old.keys() & new.keys()):
        a = {f: getattr(old[key], f) for f in _COMPARED}
        b = {f: getattr(new[key], f) for f in _COMPARED}
        if a != b:
            changed.append({"key": list(key), "before": a, "after": b})
    return {
        "added": [new[k].to_json() for k in sorted(new.keys() - old.keys())],
        "removed": [old[k].to_json() for k in sorted(old.keys() - new.keys())],
        "changed": changed,
    }
