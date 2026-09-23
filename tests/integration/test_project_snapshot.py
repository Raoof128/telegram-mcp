"""The project-scoped snapshot: readable set, bounds, drift, egress, cursors."""

import pytest

from telegram_mcp.disclosure.budget import GLOBAL, PROJECT
from telegram_mcp.disclosure.coordinator import AuthorityRefusal
from telegram_mcp.disclosure.seams import CoordinatorAuthority, ProjectSnapshot
from telegram_mcp.keys.store import load_key, provision_missing
from telegram_mcp.runtime.identity import resolve_principal
from telegram_mcp.storage.db import bind_cursor_store, open_db
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows, seed_project_world

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def world(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
    )
    return conn, authority, resolve_principal(conn, CLIENT), refs


def _snap(authority, principal, tool, **args):
    args.setdefault("project_ref", PROJECT_REF)
    args.setdefault("limit", 20)
    request = authority.freeze_arguments(tool, args, principal=principal)
    return authority.snapshot(tool, request)


def _bump_policy(conn):
    conn.execute("UPDATE policy_state SET policy_epoch = policy_epoch + 1")
    conn.commit()


def test_the_readable_set_is_members_that_pass_owner_policy(world):
    conn, authority, principal, _refs = world
    snap = _snap(authority, principal, "telegram_list_chats")
    assert isinstance(snap, ProjectSnapshot)
    assert snap.readable == {"user:100", "channel:7", "chat:9"}  # Bob is allowed, not a member
    assert snap.project_names == ("Alpha",) and snap.egress_level == "full_text"
    assert snap.project_scope_digest == "hmac-sha256:" + snap.scope_hex
    conn.execute("UPDATE peer_policy SET decision = 'deny' WHERE telegram_peer_type = 'channel'")
    _bump_policy(conn)
    assert "channel:7" not in _snap(authority, principal, "telegram_list_chats").readable


def test_get_messages_requires_a_readable_peer(world):
    _conn, authority, principal, refs = world
    ok = _snap(authority, principal, "telegram_get_messages", peer_ref=refs["user:100"])
    assert (ok.peer_identity, ok.peer_name) == ("user:100", "Ali")
    with pytest.raises(AuthorityRefusal) as exc:
        _snap(authority, principal, "telegram_get_messages", peer_ref=refs["user:101"])
    assert exc.value.code == "NOT_ACCESSIBLE"
    with pytest.raises(AuthorityRefusal) as exc:
        _snap(authority, principal, "telegram_get_messages", peer_ref="tgp_" + "z" * 26)
    assert exc.value.code == "REF_NOT_FOUND"


def test_budget_attribution_follows_the_contracts(world):
    _conn, authority, principal, _refs = world
    for tool, kinds in (
        ("telegram_list_chats", {GLOBAL, PROJECT}),
        ("telegram_get_unread", {GLOBAL}),
        ("telegram_resolve_peer", {GLOBAL}),
    ):
        extra = {"query": "a"} if tool == "telegram_resolve_peer" else {}
        snap = _snap(authority, principal, tool, **extra)
        buckets = authority.worst_case_buckets(tool, snap)
        assert {key.kind for key in buckets} == kinds, tool
        assert all(usage.records == 20 for usage in buckets.values())


def test_revalidate_sees_policy_and_membership_drift(world):
    conn, authority, principal, _refs = world
    snap = _snap(authority, principal, "telegram_list_chats")
    assert authority.revalidate(snap) is None
    _bump_policy(conn)
    assert authority.revalidate(snap) == "POLICY_CHANGED"
    snap = _snap(authority, principal, "telegram_list_chats")
    conn.execute("UPDATE projects SET project_epoch = project_epoch + 1")
    conn.commit()
    assert authority.revalidate(snap) == "POLICY_CHANGED"


def test_egress_truncates_messages_under_an_excerpt_grant(world):
    conn, authority, principal, refs = world
    conn.execute("UPDATE client_projects SET egress_level = 'excerpt', excerpt_max_codepoints = 64")
    conn.commit()
    snap = _snap(authority, principal, "telegram_get_messages", peer_ref=refs["user:100"])
    raw = {"messages": [{"text": "x" * 100, "text_truncated": False}], "project": {}}
    out = authority.apply_egress(raw, snap)
    assert out["messages"][0] == {"text": "x" * 64, "text_truncated": True}
    assert raw["messages"][0]["text"] == "x" * 100  # the input is not mutated


def test_a_project_cursor_carries_state_and_dies_with_policy(world):
    conn, authority, principal, _refs = world
    args = {"project_ref": PROJECT_REF, "limit": 20, "chat_type": "any", "archived": "exclude"}
    snap = _snap(authority, principal, "telegram_list_chats", **args)
    cursor = authority.mint_project_cursor(snap, args, {"seen_ids": [1, 3]})
    again = _snap(authority, principal, "telegram_list_chats", cursor=cursor, **args)
    assert dict(again.state) == {"seen_ids": [1, 3]}
    _bump_policy(conn)
    with pytest.raises(AuthorityRefusal) as exc:
        _snap(authority, principal, "telegram_list_chats", cursor=cursor, **args)
    assert exc.value.code == "CURSOR_POLICY_CHANGED"


def test_4c_tools_still_refuse_before_any_prompt(world):
    _conn, authority, principal, _refs = world
    with pytest.raises(AuthorityRefusal) as exc:
        authority.freeze_arguments("telegram_get_context", {}, principal=principal)
    assert exc.value.code == "POLICY_UNCONFIGURED"
