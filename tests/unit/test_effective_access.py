"""Metadata-effective access (design §2.3): refs only, deterministic, diffable."""

import json

import pytest

from comms.transports.telegram.authority.effective import diff_rows, rows_digest
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.effective_access import base_digest, explain, snapshot
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows, seed_project_world

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    return conn, refs


def test_one_project_row_and_two_rows_per_member(world):
    conn, _refs = world
    rows = snapshot(conn)
    assert len(rows) == 1 + 3 * 2  # discover row + (read, cross_search) x 3 members
    assert [r.key for r in rows] == sorted(r.key for r in rows)


def test_decisions_follow_the_one_evaluator(world):
    conn, refs = world
    by_key = {r.key: r for r in snapshot(conn)}
    ali = by_key[(CLIENT, PROJECT_REF, refs["user:100"], "read")]
    assert (ali.decision, ali.egress, ali.facts) == ("allow", "full_text", "unknown")
    assert ali.owner_class == "unknown"  # archive state is never stored: conditional allow
    team = by_key[(CLIENT, PROJECT_REF, refs["chat:9"], "read")]
    assert team.decision == "allow"  # include_groups = 1
    cross = by_key[(CLIENT, PROJECT_REF, refs["user:100"], "cross_search")]
    assert cross.decision == "NOT_ACCESSIBLE"  # the grant has can_cross_search = 0


IDENTITIES = {"user:100", "user:101", "channel:7", "chat:9"}
RAW_IDS = {100, 101, 7, 9, "100", "101", "7", "9"}


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _values(v)
    else:
        yield obj


def test_no_row_carries_a_raw_identity(world):
    conn, _refs = world
    produced = [r.to_json() for r in snapshot(conn)]
    produced += explain(conn, client_ref=CLIENT, project_ref=PROJECT_REF)
    for value in _values(produced):
        assert value not in IDENTITIES and value not in RAW_IDS, value
        if isinstance(value, str):
            assert not any(identity in value for identity in IDENTITIES), value
    assert "user:100" not in json.dumps(produced)


def test_digest_is_stable_and_moves_with_policy(world):
    conn, _refs = world
    first = rows_digest(snapshot(conn))
    assert rows_digest(snapshot(conn)) == first
    base = base_digest(conn)
    conn.execute("UPDATE client_projects SET egress_level = 'metadata_only'")
    conn.commit()
    assert rows_digest(snapshot(conn)) != first
    assert base_digest(conn) != base


def test_diff_reports_changed_rows_only(world):
    conn, refs = world
    before = snapshot(conn)
    conn.execute("UPDATE client_projects SET egress_level = 'metadata_only'")
    conn.commit()
    diff = diff_rows(before, snapshot(conn))
    assert diff["added"] == [] and diff["removed"] == []
    changed = {tuple(c["key"]) for c in diff["changed"]}
    assert (CLIENT, PROJECT_REF, refs["user:100"], "read") in changed
    assert all(c["before"]["egress"] == "full_text" for c in diff["changed"])


def test_an_archive_switch_is_a_visible_change(world):
    conn, refs = world
    before = snapshot(conn)
    conn.execute("UPDATE policy_state SET include_archived = 1")
    conn.commit()
    changed = {tuple(c["key"]): c for c in diff_rows(before, snapshot(conn))["changed"]}
    ali = changed[(CLIENT, PROJECT_REF, refs["user:100"], "read")]
    assert ali["before"]["owner_class"] == "unknown" and ali["after"]["owner_class"] == "pass"


def test_a_channel_excluded_either_way_is_denied_not_unknown(world):
    conn, refs = world
    conn.execute("UPDATE policy_state SET include_groups = 0, include_channels = 0")
    conn.commit()
    news = {r.key: r for r in snapshot(conn)}[(CLIENT, PROJECT_REF, refs["channel:7"], "read")]
    assert (news.decision, news.owner_class) == ("NOT_ACCESSIBLE", "deny")


def test_explain_carries_the_ordered_trace(world):
    conn, refs = world
    rows = explain(conn, client_ref=CLIENT, project_ref=PROJECT_REF, peer_ref=refs["user:100"])
    read = next(r for r in rows if r["operation"] == "read")
    assert [s for s, _ in read["trace"]][:3] == ["client", "project", "client_enabled"]
    assert read["trace"][-1] == ["egress", "full_text"]
