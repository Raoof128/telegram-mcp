from typing import Any

import pytest

from telegram_mcp.authority.policy import (
    AuthorityChanged,
    AuthorityRequest,
    AuthoritySnapshot,
    ClientProjectGrant,
    ClientState,
    Denial,
    ProjectState,
    check_pre_serialize,
    evaluate,
    make_view,
)


def _full_grant(**over) -> ClientProjectGrant:
    kw: dict[str, Any] = {
        "can_read": True,
        "can_cross_search": True,
        "egress_level": "full_text",
        "excerpt_limit": None,
        "grant_digest": "0" * 64,
    }
    kw.update(over)
    return ClientProjectGrant(**kw)


def _base_view(**over):
    kw = {
        "owner_allows": {"peer1"},
        "owner_denies": set(),
        "memberships": {"p1": {"peer1"}, "p2": {"peer1"}},
        "clients": {
            "c1": ClientState(client_ref="c1", enabled=True, principal_ref="prn_" + "a" * 26)
        },
        "projects": {
            "p1": ProjectState(project_ref="p1", enabled=True, project_epoch=1),
            "p2": ProjectState(project_ref="p2", enabled=True, project_epoch=2),
        },
        "grants": {
            ("c1", "p1"): _full_grant(),
            ("c1", "p2"): _full_grant(grant_digest="1" * 64),
        },
        "policy_epoch": 1,
        "security_epoch": 1,
    }
    kw.update(over)
    return make_view(**kw)


def _read(*refs, peer="peer1", operation="read"):
    return AuthorityRequest(
        operation=operation, client_ref="c1", project_refs=tuple(refs), peer_identity=peer
    )


def test_deny_beats_allow_at_every_layer():
    view = make_view(
        owner_allows={"peer1"},
        owner_denies={"peer1"},
        memberships={"p1": {"peer1"}},
        clients={"c1": ClientState(client_ref="c1", enabled=True, principal_ref="prn_" + "a" * 26)},
        projects={"p1": ProjectState(project_ref="p1", enabled=True, project_epoch=1)},
        grants={
            ("c1", "p1"): ClientProjectGrant(
                can_read=True,
                can_cross_search=False,
                egress_level="full_text",
                excerpt_limit=None,
                grant_digest="0" * 64,
            )
        },
        policy_epoch=1,
        security_epoch=1,
    )
    result = evaluate(
        view,
        AuthorityRequest(
            operation="read", client_ref="c1", project_refs=("p1",), peer_identity="peer1"
        ),
    )
    assert isinstance(result, Denial)


def test_all_allow_returns_snapshot_with_epochs_and_digests():
    result = evaluate(_base_view(), _read("p1", "p2"))
    assert isinstance(result, AuthoritySnapshot)
    assert result.policy_epoch == 1
    assert result.security_epoch == 1
    assert result.project_epochs == {"p1": 1, "p2": 2}
    assert result.grant_digests == {"p1": "0" * 64, "p2": "1" * 64}
    assert result.peer_identity == "peer1"
    assert result.effective_egress.level == "full_text"
    assert result.effective_egress.excerpt_limit is None


@pytest.mark.parametrize(
    "layer",
    ["grant", "owner", "membership"],
)
def test_deny_precedence_matrix_single_layer_deny(layer):
    """Allow everywhere except one layer: that layer's deny still denies."""
    grants = {("c1", "p1"): _full_grant(), ("c1", "p2"): _full_grant(grant_digest="1" * 64)}
    allows: set[str] = {"peer1"}
    denies: set[str] = set()
    memberships: dict[str, set[str]] = {"p1": {"peer1"}, "p2": {"peer1"}}
    if layer == "grant":
        grants[("c1", "p1")] = _full_grant(can_read=False)
    elif layer == "owner":
        denies = {"peer1"}
    else:
        memberships = {"p1": set(), "p2": {"peer1"}}
    view = _base_view(
        owner_allows=allows, owner_denies=denies, memberships=memberships, grants=grants
    )
    result = evaluate(view, _read("p1", "p2"))
    assert isinstance(result, Denial)


def test_owner_allow_list_restricts_unlisted_peer():
    result = evaluate(_base_view(), _read("p1", peer="peer2"))
    assert isinstance(result, Denial)


def test_shared_peer_minimum_egress():
    grants = {
        ("c1", "p1"): _full_grant(),
        ("c1", "p2"): _full_grant(egress_level="excerpt", excerpt_limit=200, grant_digest="2" * 64),
    }
    result = evaluate(_base_view(grants=grants), _read("p1", "p2"))
    assert isinstance(result, AuthoritySnapshot)
    assert result.effective_egress.level == "excerpt"
    assert result.effective_egress.excerpt_limit == 200


def test_smallest_excerpt_limit_wins():
    grants = {
        ("c1", "p1"): _full_grant(egress_level="excerpt", excerpt_limit=500),
        ("c1", "p2"): _full_grant(egress_level="excerpt", excerpt_limit=100, grant_digest="1" * 64),
    }
    result = evaluate(_base_view(grants=grants), _read("p1", "p2"))
    assert isinstance(result, AuthoritySnapshot)
    assert (result.effective_egress.level, result.effective_egress.excerpt_limit) == (
        "excerpt",
        100,
    )


def test_metadata_only_is_floor():
    grants = {
        ("c1", "p1"): _full_grant(),
        ("c1", "p2"): _full_grant(egress_level="metadata_only", grant_digest="1" * 64),
    }
    result = evaluate(_base_view(grants=grants), _read("p1", "p2"))
    assert isinstance(result, AuthoritySnapshot)
    assert result.effective_egress.level == "metadata_only"


def test_missing_grant_row_denies_without_default_create():
    view = _base_view(grants={("c1", "p1"): _full_grant()})
    result = evaluate(view, _read("p1", "p2"))
    assert isinstance(result, Denial)
    assert result.code == "NOT_ACCESSIBLE"
    assert ("c1", "p2") not in view.grants


def test_unknown_project_is_ref_not_found():
    result = evaluate(_base_view(), _read("nope"))
    assert isinstance(result, Denial)
    assert result.code == "REF_NOT_FOUND"


def test_unknown_client_is_ref_not_found():
    req = AuthorityRequest(
        operation="read", client_ref="ghost", project_refs=("p1",), peer_identity="peer1"
    )
    result = evaluate(_base_view(), req)
    assert isinstance(result, Denial)
    assert result.code == "REF_NOT_FOUND"


def test_disabled_project_denies():
    projects = {
        "p1": ProjectState(project_ref="p1", enabled=False, project_epoch=1),
        "p2": ProjectState(project_ref="p2", enabled=True, project_epoch=2),
    }
    result = evaluate(_base_view(projects=projects), _read("p1"))
    assert isinstance(result, Denial)


def test_disabled_client_denies_with_client_revoked():
    clients = {"c1": ClientState(client_ref="c1", enabled=False, principal_ref="prn_" + "a" * 26)}
    result = evaluate(_base_view(clients=clients), _read("p1"))
    assert isinstance(result, Denial)
    assert result.code == "CLIENT_REVOKED"


def test_cross_search_requires_cross_flag():
    grants = {
        ("c1", "p1"): _full_grant(can_cross_search=False),
        ("c1", "p2"): _full_grant(can_cross_search=False, grant_digest="1" * 64),
    }
    result = evaluate(_base_view(grants=grants), _read("p1", "p2", operation="cross_search"))
    assert isinstance(result, Denial)
    allowed = evaluate(_base_view(), _read("p1", "p2", operation="cross_search"))
    assert isinstance(allowed, AuthoritySnapshot)


def test_read_without_peer_denies_discover_without_peer_allows():
    denied = evaluate(_base_view(), _read("p1", peer=None))
    assert isinstance(denied, Denial)
    allowed = evaluate(_base_view(), _read("p1", peer=None, operation="discover"))
    assert isinstance(allowed, AuthoritySnapshot)
    assert allowed.peer_identity is None


def test_discover_ignores_capability_flags_but_requires_row():
    grants = {
        ("c1", "p1"): _full_grant(can_read=False, can_cross_search=False),
    }
    allowed = evaluate(_base_view(grants=grants), _read("p1", operation="discover"))
    assert isinstance(allowed, AuthoritySnapshot)
    missing = evaluate(_base_view(grants={}), _read("p1", operation="discover"))
    assert isinstance(missing, Denial)


def test_no_layer_expands_another():
    """Owner allow and membership never substitute for a missing grant row."""
    view = _base_view(grants={})
    assert isinstance(evaluate(view, _read("p1")), Denial)
    assert isinstance(evaluate(view, _read("p1", operation="discover")), Denial)


def test_empty_project_set_allows_nothing_with_metadata_floor():
    result = evaluate(_base_view(), _read())
    assert isinstance(result, AuthoritySnapshot)
    assert result.project_epochs == {}
    assert result.grant_digests == {}
    assert result.effective_egress.level == "metadata_only"


def test_pre_serialize_clean_passes():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    assert check_pre_serialize(view, snapshot, request) is None


def test_pre_serialize_project_removed_is_ref_not_found():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    projects = {"p2": ProjectState(project_ref="p2", enabled=True, project_epoch=2)}
    with pytest.raises(AuthorityChanged) as exc:
        check_pre_serialize(_base_view(projects=projects), snapshot, request)
    assert exc.value.code == "REF_NOT_FOUND"


def test_pre_serialize_client_disabled_is_client_revoked():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    clients = {"c1": ClientState(client_ref="c1", enabled=False, principal_ref="prn_" + "a" * 26)}
    with pytest.raises(AuthorityChanged) as exc:
        check_pre_serialize(_base_view(clients=clients), snapshot, request)
    assert exc.value.code == "CLIENT_REVOKED"


def test_pre_serialize_grant_revoked_is_not_accessible():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    grants = {("c1", "p2"): _full_grant(grant_digest="1" * 64)}
    with pytest.raises(AuthorityChanged) as exc:
        check_pre_serialize(_base_view(grants=grants), snapshot, request)
    assert exc.value.code == "NOT_ACCESSIBLE"


def test_pre_serialize_policy_epoch_bump_is_policy_changed():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    with pytest.raises(AuthorityChanged) as exc:
        check_pre_serialize(_base_view(policy_epoch=2), snapshot, request)
    assert exc.value.code == "POLICY_CHANGED"


def test_pre_serialize_security_epoch_bump_is_security_changed():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    with pytest.raises(AuthorityChanged) as exc:
        check_pre_serialize(_base_view(security_epoch=2), snapshot, request)
    assert exc.value.code == "SECURITY_CHANGED"


def test_pre_serialize_grant_digest_drift_is_policy_changed():
    view = _base_view()
    request = _read("p1")
    snapshot = evaluate(view, request)
    assert isinstance(snapshot, AuthoritySnapshot)
    grants = {
        ("c1", "p1"): _full_grant(grant_digest="f" * 64),
        ("c1", "p2"): _full_grant(grant_digest="1" * 64),
    }
    with pytest.raises(AuthorityChanged) as exc:
        check_pre_serialize(_base_view(grants=grants), snapshot, request)
    assert exc.value.code == "POLICY_CHANGED"


def test_authority_changed_carries_code():
    err = AuthorityChanged("POLICY_CHANGED", "drift")
    assert err.code == "POLICY_CHANGED"


def test_invalid_operation_and_egress_rejected():
    with pytest.raises(ValueError):
        AuthorityRequest(operation="write", client_ref="c1")
    with pytest.raises(ValueError):
        _full_grant(egress_level="superuser")
    with pytest.raises(ValueError):
        _full_grant(egress_level="excerpt", excerpt_limit=None)
    with pytest.raises(ValueError):
        _full_grant(egress_level="full_text", excerpt_limit=10)
