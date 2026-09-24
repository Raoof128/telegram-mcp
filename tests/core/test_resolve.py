"""comms 5b-4 Task 5: resolution with origin paths and sendability (design §3, §5.3; source §4, §31)."""

from datetime import UTC, datetime

import pytest

from comms.core.campaigns import directory as d
from comms.core.campaigns.resolve import Candidate, path_is_valid, resolve_targets
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)
BOTH = frozenset({"telegram", "whatsapp"})
TG = frozenset({"telegram"})
WA = frozenset({"whatsapp"})


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def person(conn, phone=None, tg_user=None):
    rcp = d.add_recipient(conn, now=NOW)
    points = {}
    if phone:
        points["wa"] = d.add_contact_point(conn, rcp, "whatsapp", phone, normalize=fx.wa, now=NOW)
    if tg_user:
        points["tg"] = d.add_contact_point(conn, rcp, "telegram", tg_user, normalize=fx.tg, now=NOW)
    return rcp, points


def endpoints(cands: list[Candidate]) -> list[tuple[str, tuple[str, ...]]]:
    return [(c.transport, c.endpoint_refs) for c in cands]


def test_all_locations_resolves_only_configured_enabled_locations(conn):
    locs = [d.add_location(conn, n, now=NOW) for n in ("MQ", "UNSW", "USYD")]
    groups = [
        d.add_destination(conn, loc, "telegram", f"group:{i}", "g", normalize=fx.tg, now=NOW)
        for i, loc in enumerate(locs)
    ]
    d.add_location(conn, "unconfigured", now=NOW)  # never added to the audience
    everyone = d.add_audience(conn, "all-locations", now=NOW)
    for loc in locs:
        d.add_audience_member(conn, everyone, loc)
    d.set_enabled(conn, locs[2], False)
    got = resolve_targets(conn, {"audiences": [everyone]}, TG)
    assert sorted(ref for c in got for ref in c.endpoint_refs) == sorted(groups[:2])
    assert all(
        o.path[0] == everyone and o.path[-1] == o.endpoint_ref for c in got for o in c.origins
    )


def test_disabled_destination_excluded_and_direct_destination_in_disabled_location_excluded(conn):
    loc = d.add_location(conn, "L", now=NOW)
    a = d.add_destination(conn, loc, "telegram", "group:1", "a", normalize=fx.tg, now=NOW)
    b = d.add_destination(conn, loc, "telegram", "group:2", "b", normalize=fx.tg, now=NOW)
    d.set_enabled(conn, a, False)
    assert endpoints(resolve_targets(conn, {"destinations": [a, b]}, TG)) == [("telegram", (b,))]
    d.set_enabled(conn, loc, False)
    assert resolve_targets(conn, {"destinations": [b]}, TG) == []


def test_recipient_in_two_locations_one_disabled_keeps_only_the_valid_path(conn):
    l1, l2 = d.add_location(conn, "1", now=NOW), d.add_location(conn, "2", now=NOW)
    rcp, pts = person(conn, phone="+61400000001")
    d.add_location_member(conn, l1, rcp)
    d.add_location_member(conn, l2, rcp)
    d.set_enabled(conn, l2, False)
    [cand] = resolve_targets(conn, {"locations": [l1, l2]}, WA)
    assert [o.path for o in cand.origins] == [(l1, rcp, pts["wa"])]


def test_opted_out_or_disabled_contact_point_or_recipient_excluded(conn):
    r1, p1 = person(conn, phone="+61400000001")
    r2, p2 = person(conn, phone="+61400000002")
    r3, _ = person(conn, phone="+61400000003")
    d.opt_out(conn, p1["wa"], now=NOW)
    d.set_enabled(conn, p2["wa"], False)
    d.set_enabled(conn, r3, False)
    assert resolve_targets(conn, {"recipients": [r1, r2, r3]}, WA) == []


def test_duplicate_whatsapp_recipient_through_four_audiences_is_one_candidate(conn):
    rcp, pts = person(conn, phone="+61400000001")
    auds = [d.add_audience(conn, str(i), now=NOW) for i in range(4)]
    for aud in auds:
        d.add_audience_member(conn, aud, rcp)
    [cand] = resolve_targets(conn, {"audiences": auds}, WA)
    assert cand.endpoint_refs == (pts["wa"],)
    assert len(cand.origins) == 4


def test_same_person_via_contact_point_and_private_chat_destination_is_one_candidate_with_both_endpoint_refs(
    conn,
):
    loc = d.add_location(conn, "L", now=NOW)
    dm = d.add_destination(conn, loc, "telegram", "private:12345", "DM", normalize=fx.tg, now=NOW)
    rcp, pts = person(conn, tg_user="user:12345")
    [cand] = resolve_targets(conn, {"destinations": [dm], "recipients": [rcp]}, TG)
    assert cand.endpoint_refs == tuple(sorted((dm, pts["tg"])))
    assert {o.endpoint_ref for o in cand.origins} == {dm, pts["tg"]}


def test_a_group_sharing_the_raw_number_is_a_different_candidate(conn):
    loc = d.add_location(conn, "L", now=NOW)
    group = d.add_destination(conn, loc, "telegram", "group:12345", "G", normalize=fx.tg, now=NOW)
    rcp, _ = person(conn, tg_user="user:12345")
    assert len(resolve_targets(conn, {"destinations": [group], "recipients": [rcp]}, TG)) == 2


def test_same_person_on_both_transports_is_two_candidates_and_only_requested_transports(conn):
    rcp, _ = person(conn, phone="+61400000001", tg_user="user:5")
    assert [c.transport for c in resolve_targets(conn, {"recipients": [rcp]}, BOTH)] == [
        "telegram",
        "whatsapp",
    ]
    assert [c.transport for c in resolve_targets(conn, {"recipients": [rcp]}, WA)] == ["whatsapp"]


def test_a_location_resolves_its_destinations_and_members(conn):
    loc = d.add_location(conn, "MQ", now=NOW)
    group = d.add_destination(conn, loc, "telegram", "group:1", "g", normalize=fx.tg, now=NOW)
    rcp, pts = person(conn, phone="+61400000001")
    d.add_location_member(conn, loc, rcp)
    got = resolve_targets(conn, {"locations": [loc]}, BOTH)
    assert endpoints(got) == [("telegram", (group,)), ("whatsapp", (pts["wa"],))]


@pytest.mark.parametrize(
    "targets",
    [
        {"audiences": ["cau_" + "a" * 26]},
        {"locations": ["rcp_" + "a" * 26]},
        {"recipients": ["not a ref"]},
        {"everyone": []},
        {"audiences": "cau_" + "a" * 26},
    ],
)
def test_unknown_targets_fail_closed(conn, targets):
    with pytest.raises(d.DirectoryError, match="^unknown target$"):
        resolve_targets(conn, targets, BOTH)


def test_a_planted_cycle_fails_closed(conn):
    a, b = d.add_audience(conn, "a", now=NOW), d.add_audience(conn, "b", now=NOW)
    d.add_audience_member(conn, a, b)
    conn.execute(
        "INSERT INTO audience_members (audience_id, member_audience_id) VALUES "
        "((SELECT id FROM audiences WHERE ref = ?), (SELECT id FROM audiences WHERE ref = ?))",
        (b, a),
    )
    with pytest.raises(d.DirectoryError, match="^audience cycle$"):
        resolve_targets(conn, {"audiences": [a]}, BOTH)


def test_a_diamond_is_not_a_cycle(conn):
    top, left, right = (d.add_audience(conn, n, now=NOW) for n in ("t", "l", "r"))
    rcp, _ = person(conn, phone="+61400000001")
    d.add_audience_member(conn, top, left)
    d.add_audience_member(conn, top, right)
    d.add_audience_member(conn, left, rcp)
    d.add_audience_member(conn, right, rcp)
    [cand] = resolve_targets(conn, {"audiences": [top]}, WA)
    assert len(cand.origins) == 2


def test_path_validity_follows_every_node_and_edge(conn):
    outer, inner = d.add_audience(conn, "o", now=NOW), d.add_audience(conn, "i", now=NOW)
    loc = d.add_location(conn, "L", now=NOW)
    rcp, pts = person(conn, phone="+61400000001")
    d.add_audience_member(conn, outer, inner)
    d.add_audience_member(conn, inner, loc)
    d.add_location_member(conn, loc, rcp)
    [cand] = resolve_targets(conn, {"audiences": [outer]}, WA)
    [o] = cand.origins
    assert o.path == (outer, inner, loc, rcp, pts["wa"]) and path_is_valid(conn, o.path)
    d.remove_location_member(conn, loc, rcp)
    assert not path_is_valid(conn, o.path)
    d.add_location_member(conn, loc, rcp)
    d.remove_audience_member(conn, inner, loc)
    assert not path_is_valid(conn, o.path)
    d.add_audience_member(conn, inner, loc)
    for node in (loc, rcp, pts["wa"]):
        d.set_enabled(conn, node, False)
        assert not path_is_valid(conn, o.path)
        d.set_enabled(conn, node, True)
    assert path_is_valid(conn, o.path)
    d.opt_out(conn, pts["wa"], now=NOW)
    assert not path_is_valid(conn, o.path)


def test_a_direct_destination_path_fails_when_its_location_is_disabled(conn):
    loc = d.add_location(conn, "L", now=NOW)
    dst = d.add_destination(conn, loc, "telegram", "group:1", "g", normalize=fx.tg, now=NOW)
    assert path_is_valid(conn, (dst,))
    d.set_enabled(conn, loc, False)
    assert not path_is_valid(conn, (dst,))


def test_malformed_paths_are_invalid(conn):
    rcp, pts = person(conn, phone="+61400000001")
    assert not path_is_valid(conn, ())
    assert not path_is_valid(conn, ("junk",))
    assert not path_is_valid(conn, (pts["wa"], rcp))  # edge in the wrong direction
    assert not path_is_valid(conn, ("rct_" + "a" * 26,))  # unknown node


def test_resolution_is_deterministic(conn):
    auds = [d.add_audience(conn, str(i), now=NOW) for i in range(3)]
    for i in range(6):
        rcp, _ = person(conn, phone=f"+6140000000{i}")
        d.add_audience_member(conn, auds[i % 3], rcp)
        d.add_audience_member(conn, auds[(i + 1) % 3], rcp)
    first = resolve_targets(conn, {"audiences": auds}, WA)
    assert first == resolve_targets(conn, {"audiences": list(reversed(auds))}, WA)
