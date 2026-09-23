"""Search authority (spec §21, §21A, §23; design §4.2-§4.5)."""

import pytest

from telegram_mcp.disclosure.budget import GLOBAL, PROJECT
from telegram_mcp.disclosure.coordinator import AuthorityRefusal
from telegram_mcp.disclosure.seams import CoordinatorAuthority
from telegram_mcp.disclosure.search_authority import SearchSnapshot
from telegram_mcp.keys.store import load_key, provision_missing
from telegram_mcp.runtime.identity import resolve_principal
from telegram_mcp.storage.db import bind_cursor_store, open_db
from tests.authority_fixtures import (
    BETA_REF,
    PROJECT_REF,
    seed_authority_rows,
    seed_project_world,
    seed_second_project,
)

CLIENT = "tcl_" + "a" * 26
NOW = 1_790_000_000.0  # 2026-09-21T14:13:20Z


@pytest.fixture
def world(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    seed_second_project(conn)
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
        clock=lambda: NOW,
    )
    principal = resolve_principal(conn, CLIENT)

    def snap(tool, **args):
        args.setdefault("limit", 20)
        request = authority.freeze_arguments(tool, args, principal=principal)
        return request.validated_args, authority.snapshot(tool, request)

    return conn, authority, snap, refs, tmp_path


def _bump(conn, sql):
    conn.execute(sql)
    conn.commit()


def test_an_ordinary_search_universe_is_the_project_in_canonical_order(world):
    _conn, authority, snap, _refs, _tmp = world
    _args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle")
    assert isinstance(s, SearchSnapshot)
    assert s.universe == ("channel:7", "chat:9", "user:100") and s.peer_cap is None
    assert s.universe_digest.startswith("hmac-sha256:") and s.project_count == 1
    kinds = {key.kind for key in authority.worst_case_buckets("telegram_search_messages", s)}
    assert kinds == {GLOBAL, PROJECT}


def test_peer_ref_narrows_to_one_member(world):
    _conn, _authority, snap, refs, _tmp = world
    _args, s = snap(
        "telegram_search_messages", project_ref=PROJECT_REF, query="x", peer_ref=refs["chat:9"]
    )
    assert s.universe == ("chat:9",)
    with pytest.raises(AuthorityRefusal) as exc:
        snap(
            "telegram_search_messages",
            project_ref=PROJECT_REF,
            query="x",
            peer_ref=refs["user:101"],
        )
    assert exc.value.code == "NOT_ACCESSIBLE"
    with pytest.raises(AuthorityRefusal) as exc:
        snap(
            "telegram_search_messages",
            project_ref=PROJECT_REF,
            query="x",
            peer_ref="tgp_" + "z" * 26,
        )
    assert exc.value.code == "REF_NOT_FOUND"


def test_cross_search_unions_and_dedupes_and_charges_every_project(world):
    _conn, authority, snap, _refs, _tmp = world
    _args, s = snap(
        "telegram_cross_project_search", project_refs=[BETA_REF, PROJECT_REF], query="x"
    )
    assert s.universe == ("channel:7", "chat:9", "user:100", "user:101")  # shared channel once
    assert [p.project_ref for p in s.projects] == sorted([BETA_REF, PROJECT_REF])
    assert (
        s.peer_cap == 250 and s.project_names == ("Alpha", "Beta") and s.egress_level == "full_text"
    )
    buckets = authority.worst_case_buckets("telegram_cross_project_search", s)
    assert sum(1 for key in buckets if key.kind == PROJECT) == 2


def test_cross_search_needs_cross_permission_on_every_project(world):
    conn, _authority, snap, _refs, _tmp = world
    _bump(conn, "UPDATE client_projects SET can_cross_search = 0 WHERE project_id = 2")
    with pytest.raises(AuthorityRefusal) as exc:
        snap("telegram_cross_project_search", project_refs=[PROJECT_REF, BETA_REF], query="x")
    assert exc.value.code == "NOT_ACCESSIBLE"


def test_each_record_takes_the_most_restrictive_grant_of_its_own_projects(world):
    _conn, authority, snap, _refs, _tmp = world
    _args, s = snap(
        "telegram_cross_project_search", project_refs=[PROJECT_REF, BETA_REF], query="x"
    )
    raw = {
        "results": [
            {
                "origin_project_refs": [PROJECT_REF, BETA_REF],
                "text": "shared",
                "text_truncated": False,
            },
            {"origin_project_refs": [PROJECT_REF], "text": "alpha only", "text_truncated": False},
        ]
    }
    out = authority.apply_egress(raw, s)
    assert [r["text"] for r in out["results"]] == [None, "alpha only"]


def _continue(snap, args, cursor):
    return snap("telegram_search_messages", **{**args, "cursor": cursor})


def _first(world):
    _conn, authority, snap, _refs, _tmp = world
    args = {"project_ref": PROJECT_REF, "query": "needle"}
    validated, s = snap("telegram_search_messages", **args)
    state = {
        "upper_date": s.upper_date,
        "universe_digest": s.universe_digest,
        "window_start": 0,
        "next_unstarted_index": 1,
    }
    return args, authority.mint_search_cursor(s, validated, state), s


def test_a_cursor_resumes_with_its_state_and_frozen_anchor(world):
    args, cursor, first = _first(world)
    _validated, again = _continue(world[2], args, cursor)
    assert again.state["next_unstarted_index"] == 1 and again.upper_date == first.upper_date


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("UPDATE policy_state SET policy_epoch = policy_epoch + 1", "CURSOR_POLICY_CHANGED"),
        (
            "UPDATE projects SET project_epoch = project_epoch + 1 WHERE id = 1",
            "CURSOR_PROJECT_CHANGED",
        ),
        (
            "UPDATE client_projects SET egress_level = 'metadata_only' WHERE project_id = 1",
            "CURSOR_PROJECT_CHANGED",
        ),
        # membership moved with no epoch-visible cause: the universe digest catches it
        (
            "DELETE FROM project_peers WHERE project_id = 1 AND peer_id = (SELECT id FROM peers WHERE telegram_peer_id = 9)",
            "CURSOR_PROJECT_CHANGED",
        ),
    ],
)
def test_the_cursor_hierarchy_then_the_universe_digest(world, mutation, code):
    conn = world[0]
    args, cursor, _first_snap = _first(world)
    _bump(conn, mutation)
    with pytest.raises(AuthorityRefusal) as exc:
        _continue(world[2], args, cursor)
    assert exc.value.code == code


def test_a_shared_peer_removed_mid_flight_is_discarded(world):
    """Design §4.5 race test, at the authority seam: revalidation refuses."""
    conn, authority, snap, _refs, _tmp = world
    _args, s = snap(
        "telegram_cross_project_search", project_refs=[PROJECT_REF, BETA_REF], query="x"
    )
    _bump(
        conn,
        "DELETE FROM project_peers WHERE project_id = 2 AND peer_id = (SELECT id FROM peers WHERE telegram_peer_id = 7)",
    )
    assert authority.revalidate(s) == "POLICY_CHANGED"


def test_the_upper_anchor_is_the_earlier_of_until_and_now(world):
    _conn, _authority, snap, _refs, _tmp = world
    _a, future = snap(
        "telegram_search_messages", project_ref=PROJECT_REF, query="x", until="2030-01-01T00:00:00Z"
    )
    _b, past = snap(
        "telegram_search_messages",
        project_ref=PROJECT_REF,
        query="x",
        until="2026-01-01T00:00:00+01:00",
    )
    assert future.upper_date == "2026-09-21T14:13:20Z"
    assert past.upper_date == "2025-12-31T23:00:00Z"


def test_query_text_never_reaches_the_database(world):
    conn, _authority, _snap, _refs, tmp = world
    _args, _cursor, _s = _first(world)
    conn.commit()
    for path in tmp.glob("meta.db*"):
        assert b"needle" not in path.read_bytes()


def test_until_is_exclusive_to_the_second_and_any_rfc3339_case(world):
    _conn, _authority, snap, _refs, _tmp = world
    _a, fraction = snap(
        "telegram_search_messages",
        project_ref=PROJECT_REF,
        query="x",
        until="2026-01-01T10:00:00.5Z",
    )
    # A message sent at 10:00:00 is before 10:00:00.5 and must stay in range.
    assert fraction.upper_date == "2026-01-01T10:00:01Z"
    _b, lower = snap(
        "telegram_search_messages", project_ref=PROJECT_REF, query="x", until="2026-01-01t10:00:00z"
    )
    assert lower.upper_date == "2026-01-01T10:00:00Z"
