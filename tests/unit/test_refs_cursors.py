"""Task 6: opaque refs, keyed cursor binding, epoch operations.

Cursor codes come from spec §23.3/§23.4 and the Phase-2 design ruling: the
seven invalidation triggers map onto ``INVALID_CURSOR``,
``CURSOR_EXPIRED``, ``CURSOR_POLICY_CHANGED`` and ``CURSOR_PROJECT_CHANGED``.
"""

import hashlib
import re

import pytest

from comms.transports.telegram.authority.cursors import (
    CURSOR_TTL_S,
    CursorError,
    CursorPresenter,
    InMemoryCursorStore,
    ProjectScopeEntry,
    check_cursor,
    list_projects_scope_entries,
    mint_cursor,
    project_scope_digest,
    query_digest,
    scope_entries_from_view,
)
from comms.transports.telegram.authority.epochs import (
    PresenceRequired,
    bump_policy_epoch,
    bump_project_epoch,
    new_epoch_state,
    set_locked,
)
from comms.transports.telegram.authority.refs import REF_PREFIXES, validate_ref_format
from comms.transports.telegram.opaque import mint_opaque_ref

CURSOR_KEY = b"\x11" * 32
PRIVACY_KEY = b"\x22" * 32
RUNTIME_ID = b"\x33" * 16
PRINCIPAL = "prn_" + "a" * 26
CLIENT = "tcl_" + "b" * 26
ACCOUNT = "tga_" + "c" * 26
PROJECT = "tpr_" + "d" * 26
OTHER_PROJECT = "tpr_" + "e" * 26


def _entry(project: str = PROJECT, *, epoch: int = 1, **over: object) -> ProjectScopeEntry:
    kw: dict[str, object] = {
        "project_ref": project,
        "project_epoch": epoch,
        "can_read": True,
        "can_cross_search": False,
        "egress_level": "full_text",
        "excerpt_limit": None,
    }
    kw.update(over)
    return ProjectScopeEntry(**kw)  # type: ignore[arg-type]


def _presenter(**over: object) -> CursorPresenter:
    kw: dict[str, object] = {
        "principal": PRINCIPAL,
        "client": CLIENT,
        "account": ACCOUNT,
        "tool": "telegram_get_messages",
        "request": {"peer_ref": "tgp_" + "f" * 26, "limit": 20},
        "policy_epoch": 4,
        "security_epoch": 7,
        "scope": (_entry(),),
    }
    kw.update(over)
    return CursorPresenter(**kw)  # type: ignore[arg-type]


def _mint(store: InMemoryCursorStore, presenter: CursorPresenter, *, now: float = 1000.0) -> str:
    return mint_cursor(
        store,
        cursor_key=CURSOR_KEY,
        privacy_key=PRIVACY_KEY,
        presenter=presenter,
        state={"offset_id": 42},
        now=now,
        runtime_id=RUNTIME_ID,
    )


def _check(
    store: InMemoryCursorStore,
    ref: str,
    presenter: CursorPresenter,
    *,
    now: float = 1001.0,
    runtime_id: bytes = RUNTIME_ID,
) -> object:
    return check_cursor(
        store,
        cursor_key=CURSOR_KEY,
        privacy_key=PRIVACY_KEY,
        ref=ref,
        presenter=presenter,
        now=now,
        runtime_id=runtime_id,
    )


def _code(exc_info: pytest.ExceptionInfo[CursorError]) -> str:
    return exc_info.value.code


# --- refs -------------------------------------------------------------------


def test_ref_shape_and_uniqueness():
    refs = {mint_opaque_ref("tpr_") for _ in range(100)}
    assert len(refs) == 100
    assert all(re.fullmatch(r"tpr_[a-z2-7]{26}", r) for r in refs)


def test_every_spec_prefix_validates_and_returns_its_prefix():
    assert len(REF_PREFIXES) == 10
    for prefix in REF_PREFIXES:
        assert validate_ref_format(mint_opaque_ref(prefix)) == prefix


def test_unknown_prefix_and_malformed_bodies_reject():
    for bad in (
        "xyz_" + "a" * 26,  # not one of the ten
        "tpr_" + "a" * 25,  # short body
        "tpr_" + "a" * 27,  # long body
        "tpr_" + "A" * 26,  # uppercase
        "tpr_" + "a" * 25 + "1",  # 0/1/8/9 are outside a-z2-7
        "tpr" + "a" * 26,  # missing separator
        "tpr_" + "a" * 26 + "=",  # padded
        "",
    ):
        with pytest.raises(ValueError):
            validate_ref_format(bad)


def test_expected_prefix_mismatch_rejects():
    with pytest.raises(ValueError):
        validate_ref_format(mint_opaque_ref("tpr_"), expect="tgc_")


# --- keyed digests ----------------------------------------------------------


def test_query_digest_is_keyed_not_plain_sha256():
    request = {"query": "invoice", "limit": 20}
    digest = query_digest(CURSOR_KEY, tool="telegram_search_messages", request=request)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest != hashlib.sha256(b"invoice").hexdigest()
    assert digest != hashlib.sha256(repr(request).encode()).hexdigest()
    assert digest != query_digest(b"\x99" * 32, tool="telegram_search_messages", request=request)


def test_query_digest_binds_text_tool_and_ignores_the_cursor_field():
    base = {"query": "invoice", "limit": 20}
    mutated = {"query": "invoice ", "limit": 20}
    assert query_digest(CURSOR_KEY, tool="t", request=base) != query_digest(
        CURSOR_KEY, tool="t", request=mutated
    )
    assert query_digest(CURSOR_KEY, tool="t", request=base) != query_digest(
        CURSOR_KEY, tool="u", request=base
    )
    with_cursor = dict(base, cursor="tgc_" + "a" * 26)
    assert query_digest(CURSOR_KEY, tool="t", request=base) == query_digest(
        CURSOR_KEY, tool="t", request=with_cursor
    )


def test_scope_digest_is_keyed_order_insensitive_and_grant_sensitive():
    one = _entry(PROJECT)
    two = _entry(OTHER_PROJECT, epoch=3)
    forward = project_scope_digest(PRIVACY_KEY, (one, two))
    assert re.fullmatch(r"[0-9a-f]{64}", forward)
    assert forward == project_scope_digest(PRIVACY_KEY, (two, one))
    assert forward != project_scope_digest(b"\x99" * 32, (one, two))
    # epoch, each grant bit, egress level and excerpt width all bind
    assert forward != project_scope_digest(PRIVACY_KEY, (one, _entry(OTHER_PROJECT, epoch=4)))
    assert forward != project_scope_digest(PRIVACY_KEY, (_entry(can_read=False), two))
    assert forward != project_scope_digest(PRIVACY_KEY, (_entry(can_cross_search=True), two))
    excerpt = _entry(egress_level="excerpt", excerpt_limit=200)
    assert project_scope_digest(PRIVACY_KEY, (excerpt,)) != project_scope_digest(
        PRIVACY_KEY, (_entry(egress_level="excerpt", excerpt_limit=201),)
    )


def test_list_projects_variant_never_collides_with_a_selected_vector():
    entries = (_entry(),)
    assert project_scope_digest(PRIVACY_KEY, entries) != project_scope_digest(
        PRIVACY_KEY, entries, variant="list_projects"
    )


def test_scope_entries_come_from_the_authority_view():
    from comms.transports.telegram.authority.policy import (
        ClientProjectGrant,
        ClientState,
        ProjectState,
        make_view,
    )

    view = make_view(
        clients={CLIENT: ClientState(client_ref=CLIENT, enabled=True, principal_ref=PRINCIPAL)},
        projects={
            PROJECT: ProjectState(project_ref=PROJECT, enabled=True, project_epoch=1),
            OTHER_PROJECT: ProjectState(project_ref=OTHER_PROJECT, enabled=False, project_epoch=9),
        },
        grants={
            (CLIENT, PROJECT): ClientProjectGrant(True, False, "full_text", None, "0" * 64),
            (CLIENT, OTHER_PROJECT): ClientProjectGrant(True, True, "full_text", None, "1" * 64),
        },
    )
    assert scope_entries_from_view(view, CLIENT, (PROJECT,)) == (_entry(),)
    # list_projects binds only the currently visible enabled projects
    assert [e.project_ref for e in list_projects_scope_entries(view, CLIENT)] == [PROJECT]


# --- cursor lifecycle -------------------------------------------------------


def test_mint_returns_a_tgc_ref_and_round_trips():
    store = InMemoryCursorStore()
    presenter = _presenter()
    ref = _mint(store, presenter)
    assert validate_ref_format(ref) == "tgc_"
    record = _check(store, ref, presenter)
    assert record.state == {"offset_id": 42}
    # replay inside TTL is allowed
    assert _check(store, ref, presenter, now=1002.0).state == {"offset_id": 42}


def test_unknown_cursor_is_invalid_not_enumerating():
    store = InMemoryCursorStore()
    with pytest.raises(CursorError) as exc:
        _check(store, mint_opaque_ref("tgc_"), _presenter())
    assert _code(exc) == "INVALID_CURSOR"


def test_malformed_cursor_ref_is_invalid():
    store = InMemoryCursorStore()
    with pytest.raises(CursorError) as exc:
        _check(store, "tgc_short", _presenter())
    assert _code(exc) == "INVALID_CURSOR"


@pytest.mark.parametrize(
    "over",
    [
        {"principal": "prn_" + "z" * 26},
        {"client": "tcl_" + "z" * 26},
        {"account": "tga_" + "z" * 26},
        {"tool": "telegram_search_messages"},
        {"request": {"peer_ref": "tgp_" + "f" * 26, "limit": 21}},
    ],
)
def test_wrong_presenter_dimension_is_invalid_cursor(over):
    store = InMemoryCursorStore()
    ref = _mint(store, _presenter())
    with pytest.raises(CursorError) as exc:
        _check(store, ref, _presenter(**over))
    assert _code(exc) == "INVALID_CURSOR"


def test_restart_runtime_id_change_is_invalid_cursor():
    store = InMemoryCursorStore()
    presenter = _presenter()
    ref = _mint(store, presenter)
    with pytest.raises(CursorError) as exc:
        _check(store, ref, presenter, runtime_id=b"\x44" * 16)
    assert _code(exc) == "INVALID_CURSOR"


def test_security_epoch_advance_is_invalid_cursor_and_never_revives():
    store = InMemoryCursorStore()
    ref = _mint(store, _presenter())
    with pytest.raises(CursorError) as exc:
        _check(store, ref, _presenter(security_epoch=8))
    assert _code(exc) == "INVALID_CURSOR"
    # back at the minting epoch the row is gone: no revival after unlock
    with pytest.raises(CursorError) as again:
        _check(store, ref, _presenter())
    assert _code(again) == "INVALID_CURSOR"


def test_expiry_is_cursor_expired_at_the_ttl_boundary():
    store = InMemoryCursorStore()
    presenter = _presenter()
    ref = _mint(store, presenter, now=1000.0)
    assert _check(store, ref, presenter, now=1000.0 + CURSOR_TTL_S).state == {"offset_id": 42}
    ref2 = _mint(store, presenter, now=1000.0)
    with pytest.raises(CursorError) as exc:
        _check(store, ref2, presenter, now=1000.0 + CURSOR_TTL_S + 0.001)
    assert _code(exc) == "CURSOR_EXPIRED"


def test_policy_epoch_change_is_cursor_policy_changed():
    store = InMemoryCursorStore()
    ref = _mint(store, _presenter())
    with pytest.raises(CursorError) as exc:
        _check(store, ref, _presenter(policy_epoch=5))
    assert _code(exc) == "CURSOR_POLICY_CHANGED"


def test_project_epoch_bump_is_cursor_project_changed():
    store = InMemoryCursorStore()
    ref = _mint(store, _presenter())
    with pytest.raises(CursorError) as exc:
        _check(store, ref, _presenter(scope=(_entry(epoch=2),)))
    assert _code(exc) == "CURSOR_PROJECT_CHANGED"


def test_grant_change_without_epoch_bump_is_cursor_project_changed():
    store = InMemoryCursorStore()
    ref = _mint(store, _presenter())
    narrowed = _entry(egress_level="excerpt", excerpt_limit=200)
    with pytest.raises(CursorError) as exc:
        _check(store, ref, _presenter(scope=(narrowed,)))
    assert _code(exc) == "CURSOR_PROJECT_CHANGED"


def test_cursor_state_rejects_content_shaped_payloads():
    store = InMemoryCursorStore()
    for bad in ({"query": "invoice"}, {"text": "hello"}, {"message": "hi"}, {"username": "x"}):
        with pytest.raises(ValueError):
            mint_cursor(
                store,
                cursor_key=CURSOR_KEY,
                privacy_key=PRIVACY_KEY,
                presenter=_presenter(),
                state=bad,
                now=1000.0,
                runtime_id=RUNTIME_ID,
            )


def test_gc_purges_only_expired_rows():
    store = InMemoryCursorStore()
    presenter = _presenter()
    old = _mint(store, presenter, now=1000.0)
    fresh = _mint(store, presenter, now=1000.0 + CURSOR_TTL_S)
    assert store.purge_expired(1000.0 + CURSOR_TTL_S + 1) == 1
    assert store.get(old) is None
    assert store.get(fresh) is not None
    assert store.delete(fresh) is True
    assert store.delete(fresh) is False


# --- epochs -----------------------------------------------------------------


def test_new_state_matches_the_spec_initial_singleton():
    state = new_epoch_state()
    assert state["security_state"]["security_epoch"] == 1
    assert state["security_state"]["locked"] == 0
    assert state["security_state"]["locked_at"] is None


def test_policy_and_project_epochs_bump_monotonically():
    state = new_epoch_state(policy_epoch=3, projects={PROJECT: 1, OTHER_PROJECT: 5})
    assert bump_policy_epoch(state) == 4
    assert bump_policy_epoch(state) == 5
    assert bump_project_epoch(state, PROJECT) == 2
    assert state["projects"][OTHER_PROJECT]["project_epoch"] == 5
    with pytest.raises(KeyError):
        bump_project_epoch(state, "tpr_" + "z" * 26)


def test_lock_advances_the_security_epoch_and_unlock_needs_presence():
    state = new_epoch_state()
    assert set_locked(state, True) == 2
    assert state["security_state"]["locked"] == 1
    assert state["security_state"]["locked_at"] is not None
    with pytest.raises(PresenceRequired):
        set_locked(state, False)
    assert state["security_state"]["security_epoch"] == 2
    assert set_locked(state, False, presence=True) == 3
    assert state["security_state"]["locked"] == 0
    assert state["security_state"]["locked_at"] is None


def test_locking_twice_still_advances_the_epoch():
    state = new_epoch_state()
    assert set_locked(state, True) == 2
    assert set_locked(state, True) == 3
