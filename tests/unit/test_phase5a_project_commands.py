"""Project, scope and client commands completed in 5a (design §2.4)."""

import pytest

from telegram_mcp.ipc.handlers.clients import client_handlers
from telegram_mcp.ipc.handlers.projects import PROJECT_COMMANDS, project_handlers
from telegram_mcp.storage.db import open_db
from telegram_mcp.telegram.discovery import DiscoveryStore
from tests.authority_fixtures import (
    BETA_REF,
    PROJECT_REF,
    seed_authority_rows,
    seed_project_world,
    seed_second_project,
)

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    seed_second_project(conn)
    return conn, refs, project_handlers(conn, members=DiscoveryStore())


def _epoch(conn, ref):
    return conn.execute(
        "SELECT project_epoch FROM projects WHERE project_ref = ?", (ref,)
    ).fetchone()[0]


def test_rename_validates_and_bumps_the_epoch(world):
    conn, _refs, h = world
    before = _epoch(conn, PROJECT_REF)
    h["project rename"]({"project_ref": PROJECT_REF, "display_name": "Ops"})
    assert _epoch(conn, PROJECT_REF) == before + 1
    with pytest.raises(ValueError):
        h["project rename"]({"project_ref": PROJECT_REF, "display_name": "a\u202eb"})


def test_members_then_remove_peer(world):
    _conn, refs, h = world
    listed = h["project members"]({"project_ref": PROJECT_REF})["members"]
    assert {m["peer_ref"] for m in listed} == {refs["user:100"], refs["channel:7"], refs["chat:9"]}
    ali = next(m for m in listed if m["peer_ref"] == refs["user:100"])
    h["project remove-peer"]({"project_ref": PROJECT_REF, "handle": ali["handle"]})
    remaining = {
        m["peer_ref"] for m in h["project members"]({"project_ref": PROJECT_REF})["members"]
    }
    assert refs["user:100"] not in remaining


def test_member_handles_die_with_the_project_epoch(world):
    _conn, _refs, h = world
    listed = h["project members"]({"project_ref": PROJECT_REF})["members"]
    h["project rename"]({"project_ref": PROJECT_REF, "display_name": "Moved"})
    with pytest.raises(ValueError):
        h["project remove-peer"]({"project_ref": PROJECT_REF, "handle": listed[0]["handle"]})


def test_a_member_handle_is_bound_to_its_project(world):
    _conn, _refs, h = world
    listed = h["project members"]({"project_ref": PROJECT_REF})["members"]
    with pytest.raises(ValueError):
        h["project remove-peer"]({"project_ref": BETA_REF, "handle": listed[0]["handle"]})


def test_overlap_reports_shared_peers_and_their_marking(world):
    _conn, refs, h = world
    overlap = h["project overlap"]({})["overlaps"]
    assert [o["peer_ref"] for o in overlap] == [refs["channel:7"]]
    kinds = {p["project_ref"]: p["membership_kind"] for p in overlap[0]["projects"]}
    assert kinds == {PROJECT_REF: "primary", BETA_REF: "shared"}
    assert overlap[0]["explicitly_shared"] is True


def test_overlap_takes_zero_or_two_known_refs(world):
    _conn, refs, h = world
    pair = h["project overlap"]({"project_a": PROJECT_REF, "project_b": BETA_REF})["overlaps"]
    assert [o["peer_ref"] for o in pair] == [refs["channel:7"]]
    for bad in (
        {"project_a": PROJECT_REF},
        {"project_a": PROJECT_REF, "project_b": PROJECT_REF},
        {"project_a": PROJECT_REF, "project_b": "tpr_" + "z" * 26},
    ):
        with pytest.raises(ValueError):
            h["project overlap"](bad)


def test_instruction_carries_the_ref_and_says_it_grants_nothing(world):
    _conn, _refs, h = world
    snippet = h["project instruction"]({"project_ref": PROJECT_REF, "target": "claude"})["snippet"]
    assert PROJECT_REF in snippet and "grants no access" in snippet
    with pytest.raises(ValueError):
        h["project instruction"]({"project_ref": PROJECT_REF, "target": "other"})


def test_cross_search_grant_and_revoke(world):
    conn, _refs, h = world
    h["project revoke-cross-search"]({"project_ref": PROJECT_REF, "client_ref": CLIENT})
    flag = "SELECT can_cross_search FROM client_projects WHERE project_id = 1"
    assert conn.execute(flag).fetchone()[0] == 0
    h["project grant-cross-search"]({"project_ref": PROJECT_REF, "client_ref": CLIENT})
    assert conn.execute(flag).fetchone()[0] == 1
    assert "project grant-cross-search" in PROJECT_COMMANDS


def test_client_disable(world, tmp_path):
    conn, _refs, _h = world
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    h["client disable"]({"client": "codex_local"})
    assert conn.execute("SELECT enabled FROM mcp_clients").fetchone()[0] == 0
    with pytest.raises(ValueError):
        h["client disable"]({"client": "openai_tunnel"})
