"""Authority for the two search tools (spec §21, §21A, §23; design §4.2-§4.5).

Both searches share one snapshot shape. The universe is the canonical-sorted
set of owner-authorised member peers: of the one selected project, or the
de-duplicated union of 2-8 selected projects for cross-project search. The
cursor binds it by digest, never by list. Owner chat-kind switches need
dialog data, so the reads enforce them at retrieval; a peer they exclude is
never searched.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from telegram_mcp.authority.cursors import (
    CursorError,
    CursorStore,
    ProjectScopeEntry,
    check_cursor,
    mint_cursor,
    project_scope_digest,
    scope_entries_from_view,
)
from telegram_mcp.authority.policy import AuthorityRequest, Denial, evaluate, readable_members
from telegram_mcp.disclosure.bounds import worst_case
from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, BucketKey, Usage, subject_digest
from telegram_mcp.disclosure.coordinator import AuthorityRefusal
from telegram_mcp.disclosure.egress import intersect_profiles, transform_record
from telegram_mcp.runtime.identity import PrincipalContext
from telegram_mcp.storage.authority_view import (
    load_security,
    load_view,
    project_labels,
)
from telegram_mcp.storage.refstore import RefStore
from telegram_mcp.telegram.search import universe_digest
from telegram_mcp.validation import parse_time

__all__ = ["CROSS_PEER_CAP", "SEARCH_TOOLS", "SearchAuthority", "SearchSnapshot", "SelectedProject"]

SEARCH_TOOLS = frozenset({"telegram_search_messages", "telegram_cross_project_search"})
CROSS_PEER_CAP = 250  # design D9: max_cross_project_peers, cross-project only
_RANK = {"metadata_only": 0, "excerpt": 1, "full_text": 2}


@dataclass(frozen=True)
class SelectedProject:
    project_ref: str
    display_name: str
    egress_level: str
    excerpt_limit: int | None
    readable: frozenset[str]


@dataclass(frozen=True)
class SearchSnapshot:
    principal_id: int
    client_id: int
    account_id: int
    principal_ref: str
    client_ref: str
    account_ref: str
    client_kind: str
    security_epoch: int
    policy_epoch: int
    scope_hex: str
    scope_entries: tuple[ProjectScopeEntry, ...]
    tool_name: str
    projects: tuple[SelectedProject, ...]
    owner_scope: Any  # seams.OwnerScope
    universe: tuple[str, ...]
    peer_cap: int | None
    limit: int
    query: str  # memory only: never persisted, logged or put in a cursor
    since: str | None
    upper_date: str
    universe_digest: str
    state: Mapping[str, Any] = field(default_factory=dict)
    peer_ref: str | None = None
    peer_name: str | None = None
    partial: bool = False

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def project_scope_digest(self) -> str:
        return "hmac-sha256:" + self.scope_hex

    @property
    def project_names(self) -> tuple[str, ...]:
        return tuple(p.display_name for p in self.projects)

    @property
    def project_ref(self) -> str | None:
        return self.projects[0].project_ref if len(self.projects) == 1 else None

    @property
    def egress_level(self) -> str:
        """The widest selected grant: what the prompt must warn about."""
        return max((p.egress_level for p in self.projects), key=lambda level: _RANK[level])

    @property
    def excerpt_limit(self) -> int | None:
        limits = [
            p.excerpt_limit
            for p in self.projects
            if p.egress_level == "excerpt" and p.excerpt_limit is not None
        ]
        return max(limits) if limits else None


def _canonical(identity: str) -> tuple[str, int]:
    peer_type, _, raw = identity.partition(":")
    return peer_type, int(raw)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SearchAuthority:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        privacy_key: bytes,
        cursor_key: bytes,
        cursor_store: CursorStore,
        runtime_id: bytes,
        clock: Callable[[], float] = time.time,
        telegram_gate: Callable[[], str | None] = lambda: None,
        presenter: Callable[..., Any],
        owner_scope: Callable[[int, int], Any],
    ) -> None:
        self._conn = conn
        self._privacy_key = privacy_key
        self._cursor_key = cursor_key
        self._cursors = cursor_store
        self._runtime_id = runtime_id
        self._clock = clock
        self._gate = telegram_gate
        self._presenter = presenter
        self._owner_scope = owner_scope

    # -- step 2 -------------------------------------------------------------

    def _selected(
        self, view: Any, client_ref: str, refs: list[str], cross: bool, labels: Any
    ) -> tuple[SelectedProject, ...]:
        verdict = evaluate(view, AuthorityRequest("discover", client_ref, tuple(refs)))
        if isinstance(verdict, Denial):
            raise AuthorityRefusal(verdict.code)
        out = []
        for ref in sorted(refs):
            grant = view.grants[(client_ref, ref)]
            if not grant.can_read or (cross and not grant.can_cross_search):
                raise AuthorityRefusal("NOT_ACCESSIBLE")
            out.append(
                SelectedProject(
                    project_ref=ref,
                    display_name=labels[ref][1],
                    egress_level=grant.egress_level,
                    excerpt_limit=grant.excerpt_limit,
                    readable=readable_members(view, client_ref, ref),
                )
            )
        return tuple(out)

    def snapshot(self, tool_name: str, request: Any) -> SearchSnapshot:
        principal = request.principal
        if principal.account_id is None or principal.account_ref is None:
            raise AuthorityRefusal("POLICY_UNCONFIGURED")
        gated = self._gate()
        if gated is not None:
            raise AuthorityRefusal(gated)  # before consent
        security_epoch, locked = load_security(self._conn)
        if locked:
            raise AuthorityRefusal("SECURITY_LOCKED")
        view = load_view(
            self._conn, principal_id=principal.principal_id, account_id=principal.account_id
        )
        args = request.validated_args
        cross = tool_name == "telegram_cross_project_search"
        refs = list(args["project_refs"]) if cross else [args["project_ref"]]
        labels = project_labels(self._conn, account_id=principal.account_id)
        projects = self._selected(view, principal.client_ref, refs, cross, labels)
        union = frozenset().union(*(p.readable for p in projects))
        peer_ref = peer_name = None
        if not cross and args.get("peer_ref") is not None:
            row = RefStore(self._conn, account_id=principal.account_id).peer_by_ref(
                args["peer_ref"]
            )
            if row is None:
                raise AuthorityRefusal("REF_NOT_FOUND")
            if row.identity not in union:
                raise AuthorityRefusal("NOT_ACCESSIBLE")
            union = frozenset({row.identity})
            peer_ref, peer_name = row.peer_ref, row.display_name
        universe = tuple(sorted(union, key=_canonical))
        digest = universe_digest(self._privacy_key, universe)
        entries = scope_entries_from_view(
            view, principal.client_ref, [p.project_ref for p in projects]
        )
        state: dict[str, Any] = {}
        cursor = args.get("cursor")
        if cursor is not None:
            presenter = self._presenter(
                principal,
                tool_name,
                args,
                view.policy_epoch,
                security_epoch,
                entries,
                variant="selected",
            )
            try:
                record = check_cursor(
                    self._cursors,
                    cursor_key=self._cursor_key,
                    privacy_key=self._privacy_key,
                    ref=cursor,
                    presenter=presenter,
                    now=self._clock(),
                    runtime_id=self._runtime_id,
                )
            except CursorError as exc:
                raise AuthorityRefusal(exc.code) from None
            state = dict(record.state)
            # Design §4.3: after the frozen hierarchy, the universe must be the
            # one the walk started over, or an index would name another peer.
            if state.get("universe_digest") != digest:
                raise AuthorityRefusal("CURSOR_PROJECT_CHANGED")
        now = datetime.fromtimestamp(self._clock(), UTC)
        until = args.get("until")
        # ``until`` is exclusive and Telegram dates are whole seconds, so a
        # fractional ``until`` rounds up: a message at 10:00:00 stays before
        # an ``until`` of 10:00:00.5.
        ceiling = (
            datetime.fromtimestamp(math.ceil(parse_time(until).timestamp()), UTC) if until else None
        )
        upper = state.get("upper_date") or _iso(min(now, ceiling) if ceiling else now)
        return SearchSnapshot(
            principal_id=principal.principal_id,
            client_id=principal.client_id,
            account_id=principal.account_id,
            principal_ref=principal.principal_ref,
            client_ref=principal.client_ref,
            account_ref=principal.account_ref,
            client_kind=principal.client_kind,
            security_epoch=security_epoch,
            policy_epoch=view.policy_epoch,
            scope_hex=project_scope_digest(self._privacy_key, entries, variant="selected"),
            scope_entries=entries,
            tool_name=tool_name,
            projects=projects,
            owner_scope=self._owner_scope(principal.principal_id, principal.account_id),
            universe=universe,
            peer_cap=CROSS_PEER_CAP if cross else None,
            limit=int(args["limit"]),
            query=args["query"],
            since=args.get("since"),
            upper_date=upper,
            universe_digest=digest,
            state=state,
            peer_ref=peer_ref,
            peer_name=peer_name,
        )

    def mint_cursor(
        self, snapshot: SearchSnapshot, arguments: Mapping[str, Any], state: Mapping[str, Any]
    ) -> str:
        principal = PrincipalContext(
            principal_id=snapshot.principal_id,
            principal_ref=snapshot.principal_ref,
            client_id=snapshot.client_id,
            client_ref=snapshot.client_ref,
            client_kind=snapshot.client_kind,
            account_id=snapshot.account_id,
            account_ref=snapshot.account_ref,
        )
        presenter = self._presenter(
            principal,
            snapshot.tool_name,
            arguments,
            snapshot.policy_epoch,
            snapshot.security_epoch,
            snapshot.scope_entries,
            variant="selected",
        )
        return mint_cursor(
            self._cursors,
            cursor_key=self._cursor_key,
            privacy_key=self._privacy_key,
            presenter=presenter,
            state=state,
            now=self._clock(),
            runtime_id=self._runtime_id,
        )

    # -- step 3 -------------------------------------------------------------

    def worst_case_buckets(self, snapshot: SearchSnapshot) -> dict[BucketKey, Usage]:
        cross = snapshot.tool_name == "telegram_cross_project_search"
        first = snapshot.projects[0]
        records, total, project_bytes = worst_case(
            snapshot.tool_name,
            limit=snapshot.limit,
            project_ref=first.project_ref,
            project_display_name=first.display_name,
            egress_level=snapshot.egress_level,
            excerpt_limit=snapshot.excerpt_limit,
            projects=[(p.project_ref, p.display_name) for p in snapshot.projects] if cross else (),
        )
        buckets = {
            BucketKey(snapshot.client_id, GLOBAL, subject_digest(GLOBAL)): Usage(records, total)
        }
        for project in snapshot.projects:
            # §23C.1: a cross record counts whole in every contributing project.
            key = BucketKey(
                snapshot.client_id, PROJECT, subject_digest(PROJECT, project.project_ref)
            )
            buckets[key] = Usage(records, project_bytes)
        return buckets

    # -- step 8 -------------------------------------------------------------

    def revalidate(self, snapshot: SearchSnapshot) -> str | None:
        security_epoch, locked = load_security(self._conn)
        if locked or security_epoch != snapshot.security_epoch:
            return "SECURITY_LOCKED"
        view = load_view(
            self._conn, principal_id=snapshot.principal_id, account_id=snapshot.account_id
        )
        refs = [p.project_ref for p in snapshot.projects]
        verdict = evaluate(view, AuthorityRequest("discover", snapshot.client_ref, tuple(refs)))
        if isinstance(verdict, Denial):
            return "CLIENT_REVOKED" if verdict.code == "CLIENT_REVOKED" else "NOT_ACCESSIBLE"
        if view.policy_epoch != snapshot.policy_epoch:
            return "POLICY_CHANGED"
        entries = scope_entries_from_view(view, snapshot.client_ref, refs)
        if (
            project_scope_digest(self._privacy_key, entries, variant="selected")
            != snapshot.scope_hex
        ):
            return "POLICY_CHANGED"
        for project in snapshot.projects:
            # §23.7: membership of every peer the result may represent.
            if readable_members(view, snapshot.client_ref, project.project_ref) != project.readable:
                return "POLICY_CHANGED"
        return None

    # -- step 9 -------------------------------------------------------------

    def apply_egress(self, raw: Mapping[str, Any], snapshot: SearchSnapshot) -> dict[str, Any]:
        """§23B.2: each record takes the most restrictive grant among its own projects."""
        grants = {p.project_ref: (p.egress_level, p.excerpt_limit) for p in snapshot.projects}
        out = dict(raw)
        results = []
        for record in out.get("results", []):
            level, limit = intersect_profiles(
                [grants[ref] for ref in record["origin_project_refs"]]
            )
            results.append(transform_record(record, level, limit))
        out["results"] = results
        return out
