"""The real coordinator seams (Phase-4 design §2.3).

``CoordinatorAuthority`` answers the coordinator's authority questions from
live SQLite, fresh at step 2 and again at step 8. ``CoordinatorConsent``
(Task 6) drives the Phase-2 broker through the daemon-side prompter.

In 4b the two catalogue tools and the four project tools (``list_chats``,
``resolve_peer``, ``get_messages``, ``get_unread``) are served. The other
three refuse with ``POLICY_UNCONFIGURED`` before any prompt.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from comms.transports.telegram.authority.cursors import (
    CursorError,
    CursorPresenter,
    CursorStore,
    ProjectScopeEntry,
    check_cursor,
    list_projects_scope_entries,
    mint_cursor,
    project_scope_digest,
    scope_entries_from_view,
)
from comms.transports.telegram.authority.policy import (
    AuthorityRequest,
    AuthorityView,
    Denial,
    OwnerScope,
    evaluate,
    readable_members,
)
from comms.transports.telegram.consent.broker import ConsentBroker, ConsentError, ConsumedChallenge
from comms.transports.telegram.consent.challenge import display_digest, jcs_dumps
from comms.transports.telegram.consent.display import build_display
from comms.transports.telegram.consent.prompter import PromptDenied, Prompter, PromptUnavailable
from comms.transports.telegram.disclosure.bounds import NAME_MAX, clamp, worst_case
from comms.transports.telegram.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    Usage,
    subject_digest,
)
from comms.transports.telegram.disclosure.coordinator import AuthorityRefusal, ConsentRefusal
from comms.transports.telegram.disclosure.egress import transform_record
from comms.transports.telegram.disclosure.exposure import exposure_digest
from comms.transports.telegram.disclosure.search_authority import (
    SEARCH_TOOLS,
    SearchAuthority,
    SearchSnapshot,
)
from comms.transports.telegram.runtime.identity import PrincipalContext
from comms.transports.telegram.storage.authority_view import (
    load_security,
    load_view,
    project_labels,
)
from comms.transports.telegram.storage.refstore import RefStore

__all__ = [
    "CATALOGUE_TOOLS",
    "PROJECT_TOOLS",
    "SERVED_TOOLS",
    "CatalogueSnapshot",
    "CoordinatorAuthority",
    "CoordinatorConsent",
    "FrozenRequest",
    "IssuedConsent",
    "OwnerScope",
    "ProjectSnapshot",
    "VisibleProject",
]

CATALOGUE_TOOLS = frozenset({"telegram_list_projects", "telegram_resolve_project"})
PROJECT_TOOLS = frozenset(
    {
        "telegram_list_chats",
        "telegram_resolve_peer",
        "telegram_get_messages",
        "telegram_get_unread",
        "telegram_get_context",
    }
)
SERVED_TOOLS = CATALOGUE_TOOLS | PROJECT_TOOLS | SEARCH_TOOLS
# Design §3.8: only these two contracts let a record carry origin_project_refs.
_PROJECT_BUCKET_TOOLS = frozenset(
    {"telegram_list_chats", "telegram_get_messages", "telegram_get_context"}
)
_REQUEST_DOMAIN = b"telegram-mcp-request/v1\0"
# The longest match_kind value in E.12, used by the catalogue upper bound.
_LONGEST_MATCH_KIND = "substring_display_name"


def normalise(text: str) -> str:
    """NFC then casefold: the one comparison form for project names."""
    return unicodedata.normalize("NFC", text).casefold()


@dataclass(frozen=True)
class FrozenRequest:
    tool_name: str
    canonical_bytes: bytes
    canonical_request_hmac: str
    validated_args: Mapping[str, Any]
    principal: PrincipalContext


@dataclass(frozen=True)
class VisibleProject:
    project_ref: str
    slug: str
    display_name: str
    egress_level: str
    excerpt_max_codepoints: int | None
    can_cross_search: bool

    def record(self) -> dict[str, Any]:
        """The E.11 project entry."""
        return {
            "project_ref": self.project_ref,
            "display_name": self.display_name,
            "egress_level": self.egress_level,
            "can_cross_search": self.can_cross_search,
            "excerpt_max_codepoints": self.excerpt_max_codepoints,
        }


@dataclass(frozen=True)
class CatalogueSnapshot:
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
    visible: tuple[VisibleProject, ...]
    page: int = 0
    project_count: int = 0
    partial: bool = False
    egress_level: str = "metadata_only"

    @property
    def project_scope_digest(self) -> str:
        """The receipt's labelled form (Appendix K); the challenge uses ``scope_hex``."""
        return "hmac-sha256:" + self.scope_hex


@dataclass(frozen=True)
class ProjectSnapshot:
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
    project_ref: str
    project_display_name: str
    egress_level: str
    excerpt_limit: int | None
    readable: frozenset[str]
    view: AuthorityView
    limit: int
    tool_name: str
    peer_ref: str | None = None
    peer_identity: str | None = None
    peer_name: str | None = None
    anchor_message_id: int | None = None  # get_context only; never leaves the daemon
    state: Mapping[str, Any] = field(default_factory=dict)
    project_count: int = 1
    partial: bool = False

    @property
    def project_scope_digest(self) -> str:
        return "hmac-sha256:" + self.scope_hex

    def live_request(self, identity: str) -> AuthorityRequest:
        """What retrieval asks the one evaluator, with live facts (Phase-5 design §2.2)."""
        return AuthorityRequest("read", self.client_ref, (self.project_ref,), identity)

    @property
    def project_names(self) -> tuple[str, ...]:
        return (self.project_display_name,)


class CoordinatorAuthority:
    """Live authority for the coordinator. Reads fresh; decides nothing new."""

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
    ) -> None:
        self._conn = conn
        self._telegram_gate = telegram_gate
        self._privacy_key = privacy_key
        self._cursor_key = cursor_key
        self._cursors = cursor_store
        self._runtime_id = runtime_id
        self._clock = clock
        self._search = SearchAuthority(
            conn,
            privacy_key=privacy_key,
            cursor_key=cursor_key,
            cursor_store=cursor_store,
            runtime_id=runtime_id,
            clock=clock,
            telegram_gate=lambda: self._telegram_gate(),
            presenter=self._presenter,
        )

    # -- step 1 -------------------------------------------------------------

    def freeze_arguments(
        self, tool_name: str, arguments: Mapping[str, Any], *, principal: PrincipalContext | None
    ) -> FrozenRequest:
        if principal is None:
            raise AuthorityRefusal("AUTH_REQUIRED")
        if tool_name not in SERVED_TOOLS:
            raise AuthorityRefusal("POLICY_UNCONFIGURED")
        frozen = copy.deepcopy(dict(arguments))
        canonical = jcs_dumps(
            {
                "arguments": frozen,
                "client": principal.client_ref,
                "schema": "tg-mcp-request/v1",
                "tool": tool_name,
            }
        )
        mac = hmac.new(self._privacy_key, _REQUEST_DOMAIN + canonical, hashlib.sha256).hexdigest()
        return FrozenRequest(
            tool_name=tool_name,
            canonical_bytes=canonical,
            canonical_request_hmac=mac,
            validated_args=MappingProxyType(frozen),
            principal=principal,
        )

    # -- step 2 -------------------------------------------------------------

    def _presenter(
        self,
        principal: PrincipalContext,
        tool_name: str,
        args: Mapping[str, Any],
        policy_epoch: int,
        security_epoch: int,
        entries: tuple[ProjectScopeEntry, ...],
        variant: str = "list_projects",
    ) -> CursorPresenter:
        assert principal.account_ref is not None
        return CursorPresenter(
            principal=principal.principal_ref,
            client=principal.client_ref,
            account=principal.account_ref,
            tool=tool_name,
            request=dict(args),
            policy_epoch=policy_epoch,
            security_epoch=security_epoch,
            scope=entries,
            scope_variant=variant,
        )

    def snapshot(
        self, tool_name: str, request: FrozenRequest
    ) -> CatalogueSnapshot | ProjectSnapshot:
        if tool_name in SEARCH_TOOLS:
            return self._search.snapshot(tool_name, request)  # type: ignore[return-value]
        if tool_name in PROJECT_TOOLS:
            return self._project_snapshot(tool_name, request)
        principal = request.principal
        if principal.account_id is None or principal.account_ref is None:
            raise AuthorityRefusal("POLICY_UNCONFIGURED")
        security_epoch, locked = load_security(self._conn)
        if locked:
            raise AuthorityRefusal("SECURITY_LOCKED")
        view = load_view(
            self._conn, principal_id=principal.principal_id, account_id=principal.account_id
        )
        verdict = evaluate(view, AuthorityRequest("discover", principal.client_ref))
        if isinstance(verdict, Denial):
            raise AuthorityRefusal(verdict.code)
        entries = list_projects_scope_entries(view, principal.client_ref)
        scope_hex = project_scope_digest(self._privacy_key, entries, variant="list_projects")
        labels = project_labels(self._conn, account_id=principal.account_id)
        visible = tuple(
            sorted(
                (
                    VisibleProject(
                        project_ref=entry.project_ref,
                        slug=labels[entry.project_ref][0],
                        display_name=labels[entry.project_ref][1],
                        egress_level=entry.egress_level,
                        excerpt_max_codepoints=entry.excerpt_limit,
                        can_cross_search=entry.can_cross_search,
                    )
                    for entry in entries
                    if entry.can_read
                ),
                key=lambda v: (normalise(v.display_name), v.project_ref),
            )
        )
        page = 0
        cursor = request.validated_args.get("cursor")
        if cursor is not None:
            presenter = self._presenter(
                principal,
                tool_name,
                request.validated_args,
                view.policy_epoch,
                security_epoch,
                entries,
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
            page = int(record.state.get("page", 0))
        return CatalogueSnapshot(
            principal_id=principal.principal_id,
            client_id=principal.client_id,
            account_id=principal.account_id,
            principal_ref=principal.principal_ref,
            client_ref=principal.client_ref,
            account_ref=principal.account_ref,
            client_kind=principal.client_kind,
            security_epoch=security_epoch,
            policy_epoch=view.policy_epoch,
            scope_hex=scope_hex,
            scope_entries=entries,
            visible=visible,
            page=page,
        )

    def mint_catalogue_cursor(
        self, snapshot: CatalogueSnapshot, arguments: Mapping[str, Any], page: int
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
            "telegram_list_projects",
            arguments,
            snapshot.policy_epoch,
            snapshot.security_epoch,
            snapshot.scope_entries,
        )
        return mint_cursor(
            self._cursors,
            cursor_key=self._cursor_key,
            privacy_key=self._privacy_key,
            presenter=presenter,
            state={"page": page},
            now=self._clock(),
            runtime_id=self._runtime_id,
        )

    # -- project tools (4b) ------------------------------------------------

    @staticmethod
    def _readable(view: Any, client_ref: str, project_ref: str) -> frozenset[str]:
        return readable_members(view, client_ref, project_ref)

    def _project_snapshot(self, tool_name: str, request: FrozenRequest) -> ProjectSnapshot:
        principal = request.principal
        if principal.account_id is None or principal.account_ref is None:
            raise AuthorityRefusal("POLICY_UNCONFIGURED")
        gated = self._telegram_gate()
        if gated is not None:
            raise AuthorityRefusal(gated)  # before consent: never prompt for a read that cannot run
        security_epoch, locked = load_security(self._conn)
        if locked:
            raise AuthorityRefusal("SECURITY_LOCKED")
        view = load_view(
            self._conn, principal_id=principal.principal_id, account_id=principal.account_id
        )
        args = request.validated_args
        project_ref = args["project_ref"]
        verdict = evaluate(view, AuthorityRequest("discover", principal.client_ref, (project_ref,)))
        if isinstance(verdict, Denial):
            raise AuthorityRefusal(verdict.code)
        grant = view.grants[(principal.client_ref, project_ref)]
        if not grant.can_read:
            raise AuthorityRefusal("NOT_ACCESSIBLE")
        readable = self._readable(view, principal.client_ref, project_ref)
        entries = scope_entries_from_view(view, principal.client_ref, [project_ref])
        peer_ref = peer_identity = peer_name = None
        if tool_name == "telegram_get_messages":
            row = RefStore(self._conn, account_id=principal.account_id).peer_by_ref(
                args["peer_ref"]
            )
            if row is None:
                raise AuthorityRefusal("REF_NOT_FOUND")
            if row.identity not in readable:
                raise AuthorityRefusal("NOT_ACCESSIBLE")
            peer_ref, peer_identity = row.peer_ref, row.identity
            peer_name = clamp(row.display_name, NAME_MAX)[0]
        anchor_message_id = None
        if tool_name == "telegram_get_context":
            # §20.4: a historical ref resolves to its canonical peer, which must
            # pass *current* owner policy and membership; the ref grants nothing.
            refs = RefStore(self._conn, account_id=principal.account_id)
            located = refs.message_by_ref(args["message_ref"])
            row = refs.peer_by_row(located[0]) if located is not None else None
            if located is None or row is None:
                raise AuthorityRefusal("REF_NOT_FOUND")
            if row.identity not in readable:
                raise AuthorityRefusal("NOT_ACCESSIBLE")
            peer_ref, peer_identity = row.peer_ref, row.identity
            peer_name = clamp(row.display_name, NAME_MAX)[0]
            anchor_message_id = located[1]
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
        labels = project_labels(self._conn, account_id=principal.account_id)
        return ProjectSnapshot(
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
            project_ref=project_ref,
            project_display_name=labels[project_ref][1],
            egress_level=grant.egress_level,
            excerpt_limit=grant.excerpt_limit,
            readable=readable,
            view=view,
            limit=(
                int(args["before"]) + int(args["after"]) + 1
                if tool_name == "telegram_get_context"
                else int(args["limit"])
            ),
            tool_name=tool_name,
            peer_ref=peer_ref,
            peer_identity=peer_identity,
            peer_name=peer_name,
            anchor_message_id=anchor_message_id,
            state=state,
        )

    def mint_search_cursor(
        self, snapshot: SearchSnapshot, arguments: Mapping[str, Any], state: Mapping[str, Any]
    ) -> str:
        return self._search.mint_cursor(snapshot, arguments, state)

    def mint_project_cursor(
        self, snapshot: ProjectSnapshot, arguments: Mapping[str, Any], state: Mapping[str, Any]
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

    def _revalidate_project(self, snapshot: ProjectSnapshot) -> str | None:
        security_epoch, locked = load_security(self._conn)
        if locked or security_epoch != snapshot.security_epoch:
            return "SECURITY_LOCKED"
        view = load_view(
            self._conn, principal_id=snapshot.principal_id, account_id=snapshot.account_id
        )
        verdict = evaluate(
            view, AuthorityRequest("discover", snapshot.client_ref, (snapshot.project_ref,))
        )
        if isinstance(verdict, Denial):
            return "CLIENT_REVOKED" if verdict.code == "CLIENT_REVOKED" else "NOT_ACCESSIBLE"
        if view.policy_epoch != snapshot.policy_epoch:
            return "POLICY_CHANGED"
        entries = scope_entries_from_view(view, snapshot.client_ref, [snapshot.project_ref])
        if project_scope_digest(self._privacy_key, entries, variant="selected") != (
            snapshot.scope_hex
        ):
            return "POLICY_CHANGED"
        if self._readable(view, snapshot.client_ref, snapshot.project_ref) != snapshot.readable:
            return "POLICY_CHANGED"
        return None

    # -- step 3 -------------------------------------------------------------

    def worst_case_buckets(
        self, tool_name: str, snapshot: CatalogueSnapshot
    ) -> dict[BucketKey, Usage]:
        """An upper bound on either catalogue payload, from the visible set.

        Every emitted record is drawn from ``visible``, every emitted key is no
        longer than its counterpart here, and the longest ``match_kind`` is
        assumed for each. So the bound holds for both tools, for any page.
        """
        if isinstance(snapshot, SearchSnapshot):
            return self._search.worst_case_buckets(snapshot)
        if isinstance(snapshot, ProjectSnapshot):
            records, total, project_bytes = worst_case(
                tool_name,
                limit=snapshot.limit,
                project_ref=snapshot.project_ref,
                project_display_name=snapshot.project_display_name,
                egress_level=snapshot.egress_level,
                excerpt_limit=snapshot.excerpt_limit,
            )
            buckets = {
                BucketKey(snapshot.client_id, GLOBAL, subject_digest(GLOBAL)): Usage(records, total)
            }
            if tool_name in _PROJECT_BUCKET_TOOLS:
                key = BucketKey(
                    snapshot.client_id, PROJECT, subject_digest(PROJECT, snapshot.project_ref)
                )
                buckets[key] = Usage(records, project_bytes)
            return buckets
        bound = {
            "ambiguous": False,
            "projects": [
                dict(v.record(), match_kind=_LONGEST_MATCH_KIND) for v in snapshot.visible
            ],
        }
        key = BucketKey(snapshot.client_id, GLOBAL, subject_digest(GLOBAL))
        return {key: Usage(len(snapshot.visible), len(jcs_dumps(bound)))}

    # -- step 8 -------------------------------------------------------------

    def revalidate(self, snapshot: CatalogueSnapshot | ProjectSnapshot) -> str | None:
        if isinstance(snapshot, SearchSnapshot):
            return self._search.revalidate(snapshot)
        if isinstance(snapshot, ProjectSnapshot):
            return self._revalidate_project(snapshot)
        security_epoch, locked = load_security(self._conn)
        if locked or security_epoch != snapshot.security_epoch:
            return "SECURITY_LOCKED"
        view = load_view(
            self._conn, principal_id=snapshot.principal_id, account_id=snapshot.account_id
        )
        verdict = evaluate(view, AuthorityRequest("discover", snapshot.client_ref))
        if isinstance(verdict, Denial):
            return "CLIENT_REVOKED" if verdict.code == "CLIENT_REVOKED" else "NOT_ACCESSIBLE"
        if view.policy_epoch != snapshot.policy_epoch:
            return "POLICY_CHANGED"
        entries = list_projects_scope_entries(view, snapshot.client_ref)
        if (
            project_scope_digest(self._privacy_key, entries, variant="list_projects")
            != snapshot.scope_hex
        ):
            return "POLICY_CHANGED"
        return None

    # -- step 9 -------------------------------------------------------------

    def apply_egress(self, raw: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
        if isinstance(snapshot, SearchSnapshot):
            return self._search.apply_egress(raw, snapshot)
        out = dict(raw)
        if isinstance(snapshot, ProjectSnapshot) and "messages" in out:
            out["messages"] = [
                transform_record(message, snapshot.egress_level, snapshot.excerpt_limit)
                for message in out["messages"]
            ]
        return out  # catalogue and chat records carry no text field


@dataclass(frozen=True)
class IssuedConsent:
    handle: str
    display: dict[str, Any]


def _global(buckets: Mapping[BucketKey, Usage]) -> Usage:
    for key, usage in buckets.items():
        if key.kind == GLOBAL:
            return usage
    return Usage(0, 0)


class CoordinatorConsent:
    """Issue, prompt, consume: the coordinator's consent seam over real parts."""

    def __init__(self, broker: ConsentBroker, prompter: Prompter, *, wait_s: float = 45.0) -> None:
        self._broker = broker
        self._prompter = prompter
        self._wait_s = wait_s

    async def issue(
        self,
        *,
        tool_name: str,
        snapshot: Any,
        request: Any,
        tier: str,
        projected: Mapping[BucketKey, Usage],
        worst_case: Mapping[BucketKey, Usage],
    ) -> IssuedConsent:
        after = _global(projected)
        increment = _global(worst_case)
        display = build_display(
            tool_name=tool_name,
            client_kind=snapshot.client_kind,
            project_names=list(getattr(snapshot, "project_names", ())),
            peer_name=getattr(snapshot, "peer_name", None),
            egress_level=snapshot.egress_level,
            tier=tier,
            current=Usage(after.records - increment.records, after.bytes - increment.bytes),
            projected=after,
        )
        try:
            handle = await self._broker.issue(
                tool=tool_name,
                request_hmac=request.canonical_request_hmac,
                principal=snapshot.principal_ref,
                client=snapshot.client_ref,
                account=snapshot.account_ref,
                policy_epoch=snapshot.policy_epoch,
                project_scope_digest=snapshot.scope_hex,
                security_epoch=snapshot.security_epoch,
                display_digest=display_digest(display),
                exposure_snapshot_digest=exposure_digest(tier, projected),
            )
        except ConsentError as exc:
            raise ConsentRefusal(exc.dispatch_code) from None
        return IssuedConsent(handle=handle, display=display)

    async def consume(self, issued: IssuedConsent) -> ConsumedChallenge | None:
        handle = issued.handle
        try:
            envelope = await self._prompter.prompt(
                handle=handle,
                challenge=self._broker.challenge_bytes(handle),
                signature=self._broker.daemon_signature(handle),
                display=issued.display,
                timeout=self._wait_s,
            )
        except PromptUnavailable:
            self._broker.invalidate(handle)
            raise ConsentRefusal("CONSENT_UNAVAILABLE") from None
        except PromptDenied:
            self._broker.invalidate(handle)
            return None
        except asyncio.CancelledError:
            self._broker.invalidate(handle)
            raise
        try:
            return await self._broker.consume(handle, envelope)
        except ConsentError as exc:
            self._broker.invalidate(handle)
            if exc.dispatch_code == "CONSENT_DENIED":
                return None
            raise ConsentRefusal(exc.dispatch_code) from None

    def snapshot_matches(
        self, approval: ConsumedChallenge, *, tier: str, projected: Mapping[BucketKey, Usage]
    ) -> bool:
        return hmac.compare_digest(
            approval.exposure_snapshot_digest, exposure_digest(tier, projected)
        )
