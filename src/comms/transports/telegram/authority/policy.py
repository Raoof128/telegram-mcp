"""Central policy engine: typed authority model with intersection semantics.

Consumes an abstract view (Task 7 binds SQLite rows to it) so the engine
stays testable without storage. No layer expands another: every check can
only deny; an allow requires all layers to pass simultaneously.

Check order per plan (resolve client, validate refs, project enabled,
client enabled, capability flags, owner policy, membership intersection),
then snapshot epochs/digests for pre-serialize double evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "EGRESS_LEVELS",
    "AuthorityChanged",
    "AuthorityRequest",
    "AuthoritySnapshot",
    "AuthorityView",
    "ClientProjectGrant",
    "ClientState",
    "Denial",
    "EffectiveEgress",
    "OwnerScope",
    "PeerFacts",
    "ProjectState",
    "TraceStep",
    "admit_live",
    "check_pre_serialize",
    "evaluate",
    "evaluate_with_trace",
    "make_view",
    "readable_members",
]

OPERATIONS = ("read", "cross_search", "discover")
OWNER_MODES = ("allowlist", "all_cloud_chats")
EGRESS_LEVELS = ("metadata_only", "excerpt", "full_text")
_EGRESS_RANK = {"metadata_only": 0, "excerpt": 1, "full_text": 2}

REF_NOT_FOUND = "REF_NOT_FOUND"
CLIENT_REVOKED = "CLIENT_REVOKED"
NOT_ACCESSIBLE = "NOT_ACCESSIBLE"
POLICY_CHANGED = "POLICY_CHANGED"
SECURITY_CHANGED = "SECURITY_CHANGED"


@dataclass(frozen=True)
class ClientState:
    """Abstract client row: identity, enablement, owning principal."""

    client_ref: str
    enabled: bool
    principal_ref: str


@dataclass(frozen=True)
class ProjectState:
    """Abstract project row: identity, enablement, current epoch."""

    project_ref: str
    enabled: bool
    project_epoch: int


@dataclass(frozen=True)
class ClientProjectGrant:
    """Abstract grant row: capability flags, egress ceiling, content digest."""

    can_read: bool
    can_cross_search: bool
    egress_level: str
    excerpt_limit: int | None
    grant_digest: str

    def __post_init__(self) -> None:
        if self.egress_level not in _EGRESS_RANK:
            raise ValueError(f"unknown egress_level: {self.egress_level!r}")
        if self.egress_level == "excerpt":
            if not isinstance(self.excerpt_limit, int) or self.excerpt_limit <= 0:
                raise ValueError("excerpt grants require a positive excerpt_limit")
        elif self.excerpt_limit is not None:
            raise ValueError(f"{self.egress_level} grants must have excerpt_limit=None")


@dataclass(frozen=True)
class AuthorityRequest:
    """One authorization question: operation over a project set as a peer."""

    operation: str
    client_ref: str
    project_refs: tuple[str, ...] = ()
    peer_identity: str | None = None
    facts: PeerFacts | None = None

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise ValueError(f"unknown operation: {self.operation!r}")
        object.__setattr__(self, "project_refs", tuple(self.project_refs))


@dataclass(frozen=True)
class EffectiveEgress:
    """Most-restrictive egress across the contributing grants."""

    level: str
    excerpt_limit: int | None


@dataclass(frozen=True)
class PeerFacts:
    """What is known about a chat's §10.4 class. ``None`` means unknown (0B G14).

    ``candidates`` narrows an unknown ``chat_type``: a stored MTProto
    ``channel`` is either a broadcast channel or a supergroup, and if the
    owner excludes both, the answer is a definite no, not "unknown".
    """

    chat_type: str | None  # "private" | "group" | "supergroup" | "channel"
    archived: bool | None
    candidates: tuple[str, ...] = ()

    @classmethod
    def from_stored(cls, peer_type: str) -> PeerFacts:
        """``peers`` stores only the MTProto peer type; a channel may be a supergroup."""
        if peer_type == "channel":
            return cls(None, None, ("supergroup", "channel"))
        return cls({"user": "private", "chat": "group"}.get(peer_type), None)


@dataclass(frozen=True)
class OwnerScope:
    """The owner's §10.4 chat-kind switches; a chat must pass all of them."""

    include_archived: bool
    include_private: bool
    include_groups: bool
    include_channels: bool

    def admits(self, chat_type: str, is_archived: bool) -> bool:
        if is_archived and not self.include_archived:
            return False
        if chat_type == "private":
            return self.include_private
        if chat_type in ("group", "supergroup"):
            return self.include_groups
        return self.include_channels

    def decide(self, facts: PeerFacts) -> bool | None:
        """``admits`` when the facts settle it; ``None`` when they cannot."""
        if facts.chat_type is None:
            if facts.archived and not self.include_archived:
                return False
            if facts.candidates and not any(self.admits(c, False) for c in facts.candidates):
                return False  # excluded whichever subtype it turns out to be
            if facts.candidates and facts.archived is not None:
                verdicts = {self.admits(c, facts.archived) for c in facts.candidates}
                if len(verdicts) == 1:
                    return verdicts.pop()
            return None
        if facts.archived is None:
            if not self.admits(facts.chat_type, False):
                return False  # excluded by class whatever the archive state
            return True if self.include_archived else None
        return self.admits(facts.chat_type, facts.archived)


@dataclass(frozen=True)
class AuthoritySnapshot:
    """Allow verdict binding: epochs/digests the decision was taken under."""

    policy_epoch: int
    security_epoch: int
    project_epochs: dict[str, int] = field(default_factory=dict)
    grant_digests: dict[str, str] = field(default_factory=dict)
    peer_identity: str | None = None
    effective_egress: EffectiveEgress = field(
        default_factory=lambda: EffectiveEgress("metadata_only", None)
    )


@dataclass(frozen=True)
class Denial:
    """Deny verdict: fixed code plus a non-enumerating reason."""

    code: str
    reason: str


@dataclass(frozen=True)
class AuthorityView:
    """Abstract authority state: row maps plus owner policy and epochs."""

    clients: dict[str, ClientState] = field(default_factory=dict)
    projects: dict[str, ProjectState] = field(default_factory=dict)
    grants: dict[tuple[str, str], ClientProjectGrant] = field(default_factory=dict)
    memberships: dict[str, frozenset[str]] = field(default_factory=dict)
    owner_allows: frozenset[str] = frozenset()
    owner_denies: frozenset[str] = frozenset()
    policy_epoch: int = 0
    security_epoch: int = 0
    owner_mode: str = "allowlist"
    owner_scope: OwnerScope | None = None


class AuthorityChanged(Exception):
    """Pre-serialize revalidation failure: the world moved under the snapshot."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def make_view(
    *,
    clients: dict[str, ClientState] | None = None,
    projects: dict[str, ProjectState] | None = None,
    grants: dict[tuple[str, str], ClientProjectGrant] | None = None,
    memberships: dict[str, set[str] | frozenset[str]] | None = None,
    owner_allows: set[str] | frozenset[str] | None = None,
    owner_denies: set[str] | frozenset[str] | None = None,
    policy_epoch: int = 0,
    security_epoch: int = 0,
    owner_mode: str = "allowlist",
    owner_scope: OwnerScope | None = None,
) -> AuthorityView:
    """Build a normalized abstract view from row maps and owner policy."""
    if owner_mode not in OWNER_MODES:
        raise ValueError("unknown owner mode")
    return AuthorityView(
        clients=dict(clients or {}),
        projects=dict(projects or {}),
        grants=dict(grants or {}),
        memberships={k: frozenset(v) for k, v in (memberships or {}).items()},
        owner_allows=frozenset(owner_allows or ()),
        owner_denies=frozenset(owner_denies or ()),
        policy_epoch=policy_epoch,
        security_epoch=security_epoch,
        owner_mode=owner_mode,
        owner_scope=owner_scope,
    )


def _effective_egress(grants: list[ClientProjectGrant]) -> EffectiveEgress:
    """Most-restrictive level across grants; smallest non-None excerpt limit."""
    if not grants:
        return EffectiveEgress("metadata_only", None)
    level = min((g.egress_level for g in grants), key=lambda lv: _EGRESS_RANK[lv])
    limits = [g.excerpt_limit for g in grants if g.excerpt_limit is not None]
    return EffectiveEgress(level, min(limits) if limits else None)


TraceStep = tuple[str, str]


def _decide(
    view: AuthorityView, request: AuthorityRequest, trace: list[TraceStep] | None
) -> AuthoritySnapshot | Denial:
    def note(step: str, outcome: str = "pass") -> None:
        if trace is not None:
            trace.append((step, outcome))

    def deny(step: str, code: str, reason: str) -> Denial:
        note(step, f"deny:{code}")
        return Denial(code, reason)

    client = view.clients.get(request.client_ref)
    if client is None:
        return deny("client", REF_NOT_FOUND, "unknown client")
    note("client")
    for ref in request.project_refs:
        if ref not in view.projects:
            return deny("project", REF_NOT_FOUND, "unknown project")
    for ref in request.project_refs:
        if not view.projects[ref].enabled:
            return deny("project", NOT_ACCESSIBLE, "project disabled")
    note("project")
    if not client.enabled:
        return deny("client_enabled", CLIENT_REVOKED, "client disabled")
    note("client_enabled")

    contributing: list[ClientProjectGrant] = []
    for ref in request.project_refs:
        grant = view.grants.get((request.client_ref, ref))
        if grant is None:
            return deny("grant", NOT_ACCESSIBLE, "no grant row")
        if request.operation in ("read", "cross_search") and not grant.can_read:
            return deny("grant", NOT_ACCESSIBLE, "grant denies read")
        contributing.append(grant)
    note("grant")
    if request.operation == "cross_search":
        for ref in request.project_refs:
            if not view.grants[(request.client_ref, ref)].can_cross_search:
                return deny("cross_search", NOT_ACCESSIBLE, "grant denies cross-project search")
        note("cross_search")

    peer = request.peer_identity
    if peer is None:
        if request.operation in ("read", "cross_search"):
            return deny("peer", NOT_ACCESSIBLE, "peer identity required")
        note("peer", "skip")
    else:
        if peer in view.owner_denies:
            return deny("owner_deny", NOT_ACCESSIBLE, "owner denies peer")
        note("owner_deny")
        # §10.4: under allowlist only listed peers are readable -- an empty
        # list admits nothing. all_cloud_chats admits members unless denied.
        if view.owner_mode == "allowlist" and peer not in view.owner_allows:
            return deny("owner_allow", NOT_ACCESSIBLE, "owner does not allow peer")
        note("owner_allow")
        for ref in request.project_refs:
            if peer not in view.memberships.get(ref, frozenset()):
                return deny("membership", NOT_ACCESSIBLE, "peer not a project member")
        note("membership")
        if view.owner_scope is None or request.facts is None:
            note("owner_class", "facts_unknown")
        else:
            admitted = view.owner_scope.decide(request.facts)
            if admitted is False:
                return deny("owner_class", NOT_ACCESSIBLE, "owner scope excludes chat class")
            note("owner_class", "pass" if admitted else "facts_unknown")

    egress = _effective_egress(contributing)
    note("egress", egress.level)
    return AuthoritySnapshot(
        policy_epoch=view.policy_epoch,
        security_epoch=view.security_epoch,
        project_epochs={ref: view.projects[ref].project_epoch for ref in request.project_refs},
        grant_digests={
            ref: view.grants[(request.client_ref, ref)].grant_digest for ref in request.project_refs
        },
        peer_identity=peer,
        effective_egress=egress,
    )


def evaluate(view: AuthorityView, request: AuthorityRequest) -> AuthoritySnapshot | Denial:
    """Intersect all authority layers; allow only if every layer passes."""
    return _decide(view, request, None)


def evaluate_with_trace(
    view: AuthorityView, request: AuthorityRequest
) -> tuple[AuthoritySnapshot | Denial, tuple[TraceStep, ...]]:
    """The same decision, with the ordered steps that produced it (spec §33.1)."""
    trace: list[TraceStep] = []
    verdict = _decide(view, request, trace)
    return verdict, tuple(trace)


def admit_live(
    view: AuthorityView, request: AuthorityRequest, *, chat_type: str, archived: bool
) -> bool:
    """Retrieval's class/archive decision, made by the one evaluator (design §2.2).

    The request is re-evaluated in full with the live dialog facts, so the
    ``owner_class`` step, not the caller, decides.
    """
    live = AuthorityRequest(
        request.operation,
        request.client_ref,
        request.project_refs,
        request.peer_identity,
        facts=PeerFacts(chat_type, archived),
    )
    return not isinstance(evaluate(view, live), Denial)


def check_pre_serialize(
    current_view: AuthorityView, snapshot: AuthoritySnapshot, request: AuthorityRequest
) -> None:
    """Revalidate a snapshot against the current view; raise on any drift."""
    fresh = evaluate(current_view, request)
    if isinstance(fresh, Denial):
        raise AuthorityChanged(fresh.code, fresh.reason)
    if snapshot.peer_identity != request.peer_identity:
        raise AuthorityChanged(NOT_ACCESSIBLE, "snapshot bound to a different peer")
    if snapshot.policy_epoch != fresh.policy_epoch:
        raise AuthorityChanged(POLICY_CHANGED, "policy epoch advanced")
    if snapshot.security_epoch != fresh.security_epoch:
        raise AuthorityChanged(SECURITY_CHANGED, "security epoch advanced")
    if snapshot.project_epochs != fresh.project_epochs:
        raise AuthorityChanged(POLICY_CHANGED, "project epoch changed")
    if snapshot.grant_digests != fresh.grant_digests:
        raise AuthorityChanged(POLICY_CHANGED, "grant changed")
    if snapshot.effective_egress != fresh.effective_egress:
        raise AuthorityChanged(POLICY_CHANGED, "effective egress changed")


def readable_members(view: AuthorityView, client_ref: str, project_ref: str) -> frozenset[str]:
    """A project's members that pass every layer for ``read``: owner mode, deny, membership."""
    return frozenset(
        identity
        for identity in view.memberships.get(project_ref, frozenset())
        if not isinstance(
            evaluate(
                view, AuthorityRequest("read", client_ref, (project_ref,), peer_identity=identity)
            ),
            Denial,
        )
    )
