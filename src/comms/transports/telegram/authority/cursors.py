"""Cursor binding, keyed digests, and the four cursor codes (spec §23).

A cursor is server-minted, opaque, and bound to one principal, client,
account, tool, canonical query shape, owner policy epoch, global security
epoch and selected project scope (spec §23.1). Two keyed digests carry that
binding:

* ``query_digest`` — HMAC-SHA256 under the cursor key over the canonical
  request with ``cursor`` removed (spec §23.2). A plain SHA-256 of query
  text is forbidden because it is dictionary-guessable, so the key and the
  domain separator ``b"telegram-mcp-cursor/v1\\0"`` are mandatory.
* ``project_scope_digest`` — HMAC-SHA256 under the privacy key, domain
  ``b"telegram-mcp-scope/v1\\0"``, over the sorted selected project
  ref/epoch vector *and* the current grant bits, egress level and excerpt
  width (spec §12.2 cursors note, §10.8 last paragraph). The
  ``list_projects`` variant binds the complete visible enabled-project
  vector instead, under a distinct payload schema so the two can never
  collide.

Both are bare lowercase 64-hex here, which is what the frozen consent-wire
challenge carries. The Phase-3 disclosure receipt prints the same value
with its ``hmac-sha256:`` label (spec Appendix K); the label belongs to that
serializer, not to this value.

Check order in :func:`check_cursor` is fail-closed and non-enumerating
(spec §23.4): shape, existence and presenter identity come first and never
mutate the store, so a wrong presenter cannot learn whether a cursor exists
nor destroy one belonging to someone else. Only after the presenter matches
do the state checks run, and each of those deletes the dead row:

===============================================  =========================
trigger                                          code
===============================================  =========================
unknown/malformed ref, foreign presenter, tool,
query, restart (``runtime_id`` change)           ``INVALID_CURSOR``
TTL elapsed (15 minutes)                         ``CURSOR_EXPIRED``
security epoch advanced (lock/unlock)            ``INVALID_CURSOR``
owner policy epoch advanced                      ``CURSOR_POLICY_CHANGED``
project epoch or grant/egress change             ``CURSOR_PROJECT_CHANGED``
===============================================  =========================

``runtime_id`` binding is the Phase-2 fail-closed extension beyond §23
recorded in the design: a restart mints a new ``runtime_id``, so cursors
from a previous runtime can never be presented, and startup GC removes
their rows outright.

``state`` holds pagination only. Keys are an allowlist and values are
bounded (spec §23.5: no message bodies, no search text, no usernames, no
phone numbers); Phase 4 extends the allowlist deliberately, per tool.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from comms.transports.telegram.authority.refs import validate_ref_format
from comms.transports.telegram.consent.challenge import jcs_dumps  # single JCS implementation
from comms.transports.telegram.opaque import mint_opaque_ref

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from comms.transports.telegram.authority.policy import AuthorityView

__all__ = [
    "ALLOWED_STATE_KEYS",
    "CURSOR_EXPIRED",
    "CURSOR_POLICY_CHANGED",
    "CURSOR_PROJECT_CHANGED",
    "CURSOR_TTL_S",
    "INVALID_CURSOR",
    "CursorError",
    "CursorPresenter",
    "CursorRecord",
    "CursorStore",
    "InMemoryCursorStore",
    "ProjectScopeEntry",
    "check_cursor",
    "list_projects_scope_entries",
    "mint_cursor",
    "project_scope_digest",
    "query_digest",
    "scope_entries_from_view",
]

CURSOR_TTL_S = 900.0  # spec §23.3 default TTL: 15 minutes

INVALID_CURSOR = "INVALID_CURSOR"
CURSOR_EXPIRED = "CURSOR_EXPIRED"
CURSOR_POLICY_CHANGED = "CURSOR_POLICY_CHANGED"
CURSOR_PROJECT_CHANGED = "CURSOR_PROJECT_CHANGED"

_CURSOR_DOMAIN = b"telegram-mcp-cursor/v1\0"
_SCOPE_DOMAIN = b"telegram-mcp-scope/v1\0"

_SCOPE_VARIANTS = ("selected", "list_projects")
_CURSOR_CODES = (INVALID_CURSOR, CURSOR_EXPIRED, CURSOR_POLICY_CHANGED, CURSOR_PROJECT_CHANGED)
_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_ISO_Z_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

# spec §23.5: pagination identifiers/offsets, bounded seen-ID sets, anchors,
# and (4c) the search continuation window of design §4.3. Every allowed key has
# exactly one value rule; the table is the single source of both.
STATE_VALUE_RULES: dict[str, str] = {
    "add_offset": "int",
    "anchor_date": "date",
    "anchor_id": "int",
    "excluded_counts": "counts",
    "max_id": "int",
    "min_id": "int",
    "next_unstarted_index": "int",
    "offset_date": "date",
    "offset_id": "int",
    "offset_peer_ref": "peer_ref",
    "page": "int",
    "per_peer": "per_peer",
    "remaining": "int",
    "scanned_counts": "counts",
    "seen_ids": "seen_ids",
    "uncertain": "flag",
    "universe_digest": "hmac",
    "upper_date": "date",
    "window_start": "int",
}
ALLOWED_STATE_KEYS = frozenset(STATE_VALUE_RULES)
# A per_peer entry holds only its own search offset (design §4.3): exhaustion is
# encoded by absence, so there is no flag to spoof.
_PER_PEER_ENTRY_KEYS = frozenset({"offset_id"})
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_INT_MAX = 2**63 - 1
_SEEN_IDS_MAX = 1024
_PER_PEER_MAX = 64
_STATE_BYTES_MAX = 8192


class CursorError(Exception):
    """Cursor rejection carrying one of the four fixed spec codes."""

    def __init__(self, code: str) -> None:
        if code not in _CURSOR_CODES:
            raise ValueError("unknown cursor code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProjectScopeEntry:
    """One project's contribution to ``project_scope_digest``."""

    project_ref: str
    project_epoch: int
    can_read: bool
    can_cross_search: bool
    egress_level: str
    excerpt_limit: int | None

    def __post_init__(self) -> None:
        validate_ref_format(self.project_ref, expect="tpr_")
        if not isinstance(self.project_epoch, int) or isinstance(self.project_epoch, bool):
            raise ValueError("invalid project_epoch")  # noqa: TRY004 -- uniform ValueError on scope validation
        if self.project_epoch < 1:
            raise ValueError("invalid project_epoch")
        if self.egress_level not in ("metadata_only", "excerpt", "full_text"):
            raise ValueError("invalid egress_level")
        if self.excerpt_limit is not None and (
            not isinstance(self.excerpt_limit, int)
            or isinstance(self.excerpt_limit, bool)
            or self.excerpt_limit <= 0
        ):
            raise ValueError("invalid excerpt_limit")


@dataclass(frozen=True)
class CursorPresenter:
    """Who is presenting a cursor, for which tool, under which authority."""

    principal: str
    client: str
    account: str
    tool: str
    request: Mapping[str, Any]
    policy_epoch: int
    security_epoch: int
    scope: tuple[ProjectScopeEntry, ...] = ()
    scope_variant: str = "selected"

    def __post_init__(self) -> None:
        validate_ref_format(self.principal, expect="prn_")
        validate_ref_format(self.client, expect="tcl_")
        validate_ref_format(self.account, expect="tga_")
        if not self.tool or not isinstance(self.tool, str):
            raise ValueError("invalid tool")
        for name in ("policy_epoch", "security_epoch"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"invalid {name}")
        if self.scope_variant not in _SCOPE_VARIANTS:
            raise ValueError("invalid scope_variant")
        object.__setattr__(self, "scope", tuple(self.scope))


@dataclass(frozen=True)
class CursorRecord:
    """Stored cursor row: binding digests, epochs, bounded pagination state."""

    principal: str
    client: str
    account: str
    tool: str
    query_digest: str
    project_scope_digest: str
    policy_epoch: int
    security_epoch: int
    runtime_id: str
    created_at: float
    expires_at: float
    state: dict[str, Any] = field(default_factory=dict)


class CursorStore(Protocol):
    """Storage seam; Task 7 binds SQLite, tests use the in-memory fake."""

    def put(self, ref: str, record: CursorRecord) -> None: ...

    def get(self, ref: str) -> CursorRecord | None: ...

    def delete(self, ref: str) -> bool: ...

    def purge_expired(self, now: float) -> int: ...


class InMemoryCursorStore:
    """Process-memory cursor store (tests, and the pre-storage default)."""

    def __init__(self) -> None:
        self._rows: dict[str, CursorRecord] = {}

    def put(self, ref: str, record: CursorRecord) -> None:
        self._rows[ref] = record

    def get(self, ref: str) -> CursorRecord | None:
        return self._rows.get(ref)

    def delete(self, ref: str) -> bool:
        return self._rows.pop(ref, None) is not None

    def purge_expired(self, now: float) -> int:
        dead = [ref for ref, row in self._rows.items() if row.expires_at < now]
        for ref in dead:
            del self._rows[ref]
        return len(dead)


def _runtime_hex(runtime_id: bytes | str) -> str:
    if isinstance(runtime_id, bytes):
        if len(runtime_id) != 16:
            raise ValueError("invalid runtime_id")
        return runtime_id.hex()
    if isinstance(runtime_id, str) and re.fullmatch(r"[0-9a-f]{32}", runtime_id):
        return runtime_id
    raise ValueError("invalid runtime_id")


def _hmac_hex(key: bytes, domain: bytes, payload: bytes) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("invalid HMAC key")
    return hmac.new(key, domain + payload, hashlib.sha256).hexdigest()


def query_digest(cursor_key: bytes, *, tool: str, request: Mapping[str, Any]) -> str:
    """Keyed digest of the canonical request shape with ``cursor`` removed."""
    if not tool or not isinstance(tool, str):
        raise ValueError("invalid tool")
    canonical = {k: v for k, v in dict(request).items() if k != "cursor"}
    payload = jcs_dumps({"request": canonical, "schema": "tg-mcp-cursor-query/v1", "tool": tool})
    return _hmac_hex(cursor_key, _CURSOR_DOMAIN, payload)


def project_scope_digest(
    privacy_key: bytes,
    entries: Iterable[ProjectScopeEntry],
    *,
    variant: str = "selected",
) -> str:
    """Keyed digest of the project ref/epoch/grant vector."""
    if variant not in _SCOPE_VARIANTS:
        raise ValueError("invalid scope variant")
    rows = sorted(entries, key=lambda e: e.project_ref)
    refs = [row.project_ref for row in rows]
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate project in scope vector")
    payload = jcs_dumps(
        {
            "projects": [
                {
                    "can_cross_search": row.can_cross_search,
                    "can_read": row.can_read,
                    "egress_level": row.egress_level,
                    "excerpt_limit": row.excerpt_limit,
                    "project_epoch": row.project_epoch,
                    "project_ref": row.project_ref,
                }
                for row in rows
            ],
            "schema": "tg-mcp-project-scope/v1",
            "variant": variant,
        }
    )
    return _hmac_hex(privacy_key, _SCOPE_DOMAIN, payload)


def scope_entries_from_view(
    view: AuthorityView, client_ref: str, project_refs: Iterable[str]
) -> tuple[ProjectScopeEntry, ...]:
    """Build the selected-project vector from live authority rows."""
    entries: list[ProjectScopeEntry] = []
    for ref in project_refs:
        project = view.projects[ref]
        grant = view.grants[(client_ref, ref)]
        entries.append(
            ProjectScopeEntry(
                project_ref=project.project_ref,
                project_epoch=project.project_epoch,
                can_read=grant.can_read,
                can_cross_search=grant.can_cross_search,
                egress_level=grant.egress_level,
                excerpt_limit=grant.excerpt_limit,
            )
        )
    return tuple(sorted(entries, key=lambda e: e.project_ref))


def list_projects_scope_entries(
    view: AuthorityView, client_ref: str
) -> tuple[ProjectScopeEntry, ...]:
    """Build the ``telegram_list_projects`` vector: visible enabled projects."""
    visible = [
        ref
        for ref, project in view.projects.items()
        if project.enabled and (client_ref, ref) in view.grants
    ]
    return scope_entries_from_view(view, client_ref, sorted(visible))


def _check_state_value(key: str, value: Any, *, nested: bool = False) -> None:
    rule = STATE_VALUE_RULES[key]
    if rule == "seen_ids":
        if not isinstance(value, list) or len(value) > _SEEN_IDS_MAX:
            raise ValueError("invalid cursor state: seen_ids")
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= _INT_MAX:
                raise ValueError("invalid cursor state: seen_ids")
        return
    if rule == "per_peer":
        if nested:
            raise ValueError("invalid cursor state: per_peer nesting")
        if not isinstance(value, dict) or len(value) > _PER_PEER_MAX:
            raise ValueError("invalid cursor state: per_peer")
        for peer_ref, sub in value.items():
            validate_ref_format(peer_ref, expect="tgp_")
            if not isinstance(sub, dict) or set(sub) != _PER_PEER_ENTRY_KEYS:
                raise ValueError("invalid cursor state: per_peer")
            _check_state(sub, nested=True)
        return
    if rule == "date":
        if not isinstance(value, str) or _ISO_Z_RE.fullmatch(value) is None:
            raise ValueError(f"invalid cursor state: {key}")
        return
    if rule == "peer_ref":
        validate_ref_format(value, expect="tgp_")
        return
    if rule == "hmac":
        if not isinstance(value, str) or _HMAC_RE.fullmatch(value) is None:
            raise ValueError(f"invalid cursor state: {key}")
        return
    if rule == "counts":  # [global, *one per selected project]: 1..9 counters
        if not isinstance(value, list) or not 1 <= len(value) <= 9:
            raise ValueError(f"invalid cursor state: {key}")
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= _INT_MAX:
                raise ValueError(f"invalid cursor state: {key}")
        return
    if rule == "flag":
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError(f"invalid cursor state: {key}")
        return
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _INT_MAX:
        raise ValueError(f"invalid cursor state: {key}")


def _check_state(state: Mapping[str, Any], *, nested: bool = False) -> dict[str, Any]:
    """Allowlist keys, bound values, and reject anything content-shaped."""
    if not isinstance(state, dict):
        raise ValueError("invalid cursor state")  # noqa: TRY004 -- uniform ValueError on state validation
    unknown = set(state) - ALLOWED_STATE_KEYS
    if unknown:
        raise ValueError("invalid cursor state: unknown key")
    for key, value in state.items():
        _check_state_value(key, value, nested=nested)
    if not nested and len(jcs_dumps(dict(state))) > _STATE_BYTES_MAX:
        raise ValueError("invalid cursor state: too large")
    return dict(state)


def mint_cursor(
    store: CursorStore,
    *,
    cursor_key: bytes,
    privacy_key: bytes,
    presenter: CursorPresenter,
    state: Mapping[str, Any],
    now: float,
    runtime_id: bytes | str,
    ttl_s: float = CURSOR_TTL_S,
) -> str:
    """Mint a ``tgc_`` cursor bound to the presenter's full authority."""
    if not isinstance(now, (int, float)) or isinstance(now, bool):
        raise ValueError("invalid now")  # noqa: TRY004 -- uniform ValueError on cursor validation
    if ttl_s <= 0:
        raise ValueError("invalid ttl")
    record = CursorRecord(
        principal=presenter.principal,
        client=presenter.client,
        account=presenter.account,
        tool=presenter.tool,
        query_digest=query_digest(cursor_key, tool=presenter.tool, request=presenter.request),
        project_scope_digest=project_scope_digest(
            privacy_key, presenter.scope, variant=presenter.scope_variant
        ),
        policy_epoch=presenter.policy_epoch,
        security_epoch=presenter.security_epoch,
        runtime_id=_runtime_hex(runtime_id),
        created_at=float(now),
        expires_at=float(now) + float(ttl_s),
        state=_check_state(state),
    )
    ref = mint_opaque_ref("tgc_")
    store.put(ref, record)
    return ref


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def check_cursor(
    store: CursorStore,
    *,
    cursor_key: bytes,
    privacy_key: bytes,
    ref: str,
    presenter: CursorPresenter,
    now: float,
    runtime_id: bytes | str,
) -> CursorRecord:
    """Re-authorize a cursor; raise :class:`CursorError` with a fixed code."""
    try:
        validate_ref_format(ref, expect="tgc_")
    except ValueError:
        raise CursorError(INVALID_CURSOR) from None
    record = store.get(ref)
    if record is None:
        raise CursorError(INVALID_CURSOR)

    # Identity first, and without mutating the store: a foreign presenter
    # must learn nothing and must not be able to drop someone else's row.
    identity_ok = (
        _same(record.principal, presenter.principal)
        and _same(record.client, presenter.client)
        and _same(record.account, presenter.account)
        and _same(record.tool, presenter.tool)
    )
    if not identity_ok:
        raise CursorError(INVALID_CURSOR)
    presented_query = query_digest(cursor_key, tool=presenter.tool, request=presenter.request)
    if not _same(record.query_digest, presented_query):
        raise CursorError(INVALID_CURSOR)

    if not _same(record.runtime_id, _runtime_hex(runtime_id)):
        store.delete(ref)
        raise CursorError(INVALID_CURSOR)
    if float(now) > record.expires_at:
        store.delete(ref)
        raise CursorError(CURSOR_EXPIRED)
    if record.security_epoch != presenter.security_epoch:
        store.delete(ref)
        raise CursorError(INVALID_CURSOR)
    if record.policy_epoch != presenter.policy_epoch:
        store.delete(ref)
        raise CursorError(CURSOR_POLICY_CHANGED)
    presented_scope = project_scope_digest(
        privacy_key, presenter.scope, variant=presenter.scope_variant
    )
    if not _same(record.project_scope_digest, presented_scope):
        store.delete(ref)
        raise CursorError(CURSOR_PROJECT_CHANGED)
    return record
