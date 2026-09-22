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
    "ProjectState",
    "check_pre_serialize",
    "evaluate",
    "make_view",
]

OPERATIONS = ("read", "cross_search", "discover")
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
) -> AuthorityView:
    """Build a normalized abstract view from row maps and owner policy."""
    return AuthorityView(
        clients=dict(clients or {}),
        projects=dict(projects or {}),
        grants=dict(grants or {}),
        memberships={k: frozenset(v) for k, v in (memberships or {}).items()},
        owner_allows=frozenset(owner_allows or ()),
        owner_denies=frozenset(owner_denies or ()),
        policy_epoch=policy_epoch,
        security_epoch=security_epoch,
    )


def _effective_egress(grants: list[ClientProjectGrant]) -> EffectiveEgress:
    """Most-restrictive level across grants; smallest non-None excerpt limit."""
    if not grants:
        return EffectiveEgress("metadata_only", None)
    level = min((g.egress_level for g in grants), key=lambda lv: _EGRESS_RANK[lv])
    limits = [g.excerpt_limit for g in grants if g.excerpt_limit is not None]
    return EffectiveEgress(level, min(limits) if limits else None)


def evaluate(view: AuthorityView, request: AuthorityRequest) -> AuthoritySnapshot | Denial:
    """Intersect all authority layers; allow only if every layer passes."""
    client = view.clients.get(request.client_ref)
    if client is None:
        return Denial(REF_NOT_FOUND, "unknown client")
    for ref in request.project_refs:
        if ref not in view.projects:
            return Denial(REF_NOT_FOUND, "unknown project")
    for ref in request.project_refs:
        if not view.projects[ref].enabled:
            return Denial(NOT_ACCESSIBLE, "project disabled")
    if not client.enabled:
        return Denial(CLIENT_REVOKED, "client disabled")

    contributing: list[ClientProjectGrant] = []
    for ref in request.project_refs:
        grant = view.grants.get((request.client_ref, ref))
        if grant is None:
            return Denial(NOT_ACCESSIBLE, "no grant row")
        if request.operation in ("read", "cross_search") and not grant.can_read:
            return Denial(NOT_ACCESSIBLE, "grant denies read")
        contributing.append(grant)
    if request.operation == "cross_search":
        for ref in request.project_refs:
            grant = view.grants[(request.client_ref, ref)]
            if not grant.can_cross_search:
                return Denial(NOT_ACCESSIBLE, "grant denies cross-project search")

    peer = request.peer_identity
    if peer is None:
        if request.operation in ("read", "cross_search"):
            return Denial(NOT_ACCESSIBLE, "peer identity required")
    else:
        if peer in view.owner_denies:
            return Denial(NOT_ACCESSIBLE, "owner denies peer")
        if view.owner_allows and peer not in view.owner_allows:
            return Denial(NOT_ACCESSIBLE, "owner does not allow peer")
        for ref in request.project_refs:
            if peer not in view.memberships.get(ref, frozenset()):
                return Denial(NOT_ACCESSIBLE, "peer not a project member")

    return AuthoritySnapshot(
        policy_epoch=view.policy_epoch,
        security_epoch=view.security_epoch,
        project_epochs={ref: view.projects[ref].project_epoch for ref in request.project_refs},
        grant_digests={
            ref: view.grants[(request.client_ref, ref)].grant_digest for ref in request.project_refs
        },
        peer_identity=peer,
        effective_egress=_effective_egress(contributing),
    )


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
