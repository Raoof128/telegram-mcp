"""§10.4: allowlist means only listed peers; deny always wins."""

import pytest

from telegram_mcp.authority.policy import (
    AuthorityRequest,
    ClientProjectGrant,
    ClientState,
    Denial,
    ProjectState,
    evaluate,
    make_view,
)


def _view(**kw):
    base = {
        "clients": {"c1": ClientState("c1", True, "prn")},
        "projects": {"p1": ProjectState("p1", True, 1)},
        "grants": {("c1", "p1"): ClientProjectGrant(True, False, "full_text", None, "g")},
        "memberships": {"p1": {"user:1", "user:2"}},
        "policy_epoch": 1,
        "security_epoch": 1,
    }
    base.update(kw)
    return make_view(**base)


def _read(view, peer):
    return evaluate(view, AuthorityRequest("read", "c1", ("p1",), peer_identity=peer))


def test_an_empty_allowlist_denies_every_peer():
    verdict = _read(_view(owner_allows=set()), "user:1")
    assert isinstance(verdict, Denial) and verdict.code == "NOT_ACCESSIBLE"


def test_allowlist_admits_only_listed_peers():
    view = _view(owner_allows={"user:1"})
    assert not isinstance(_read(view, "user:1"), Denial)
    assert isinstance(_read(view, "user:2"), Denial)


def test_all_cloud_chats_admits_members_unless_denied():
    view = _view(owner_mode="all_cloud_chats", owner_denies={"user:2"})
    assert not isinstance(_read(view, "user:1"), Denial)
    assert isinstance(_read(view, "user:2"), Denial)


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        _view(owner_mode="everything")


def test_the_view_loads_the_mode(tmp_path):
    from telegram_mcp.storage.authority_view import load_view
    from telegram_mcp.storage.db import open_db
    from tests.authority_fixtures import seed_authority_rows

    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    assert load_view(conn, principal_id=1, account_id=1).owner_mode == "allowlist"
    conn.execute("UPDATE policy_state SET mode = 'all_cloud_chats'")
    conn.commit()
    assert load_view(conn, principal_id=1, account_id=1).owner_mode == "all_cloud_chats"
