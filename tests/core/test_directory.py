"""comms 5b-4 Task 4: the directory (design §3, R3, R11, S1, S2)."""

import random
from datetime import UTC, datetime

import pytest

from comms.core.campaigns import directory as d
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)
PHONE = "+61400000001"


def tg(raw: str) -> str:
    """Marked Telegram identity (S2), local to this test until the fakes exist."""
    kind, _, number = raw.partition(":")
    return {"user": number, "private": number, "group": "-" + number, "channel": "-100" + number}[
        kind
    ]


def wa(raw: str) -> str:
    return "+" + "".join(ch for ch in raw if ch.isdigit())


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_each_function_round_trips(conn):
    loc = d.add_location(conn, "Sydney", now=NOW)
    dst = d.add_destination(
        conn, loc, "telegram", "group:77", "Sydney group", normalize=tg, now=NOW
    )
    rcp = d.add_recipient(conn, now=NOW)
    rct = d.add_contact_point(conn, rcp, "whatsapp", "+61 400 000 001", normalize=wa, now=NOW)
    aud = d.add_audience(conn, "Everyone", now=NOW)
    d.add_location_member(conn, loc, rcp)
    d.add_audience_member(conn, aud, loc)
    d.add_audience_member(conn, aud, dst)
    d.add_audience_member(conn, aud, rcp)
    assert [x[:4] for x in (loc, dst, rcp, rct, aud)] == ["loc_", "dst_", "rcp_", "rct_", "cau_"]
    assert (
        conn.execute(
            "SELECT identity FROM delivery_identities WHERE transport='whatsapp'"
        ).fetchone()[0]
        == PHONE
    )
    d.set_enabled(conn, dst, False)
    d.opt_out(conn, rct, now=NOW)
    assert (
        conn.execute("SELECT opted_out_at FROM contact_points").fetchone()[0]
        == "2026-09-24T00:00:00.000000Z"
    )
    d.remove_audience_member(conn, aud, rcp)
    d.remove_location_member(conn, loc, rcp)
    assert _count(conn, "audience_members") == 2 and _count(conn, "location_members") == 0
    assert not conn.in_transaction


def test_destination_and_contact_point_share_one_identity(conn):
    loc = d.add_location(conn, "L", now=NOW)
    rcp = d.add_recipient(conn, now=NOW)
    d.add_destination(conn, loc, "telegram", "private:12345", "DM", normalize=tg, now=NOW)
    d.add_contact_point(conn, rcp, "telegram", "user:12345", normalize=tg, now=NOW)
    assert _count(conn, "delivery_identities") == 1


def test_peer_kinds_stay_distinct(conn):
    loc = d.add_location(conn, "L", now=NOW)
    for raw in ("user:5", "group:5", "channel:5"):
        d.add_destination(conn, loc, "telegram", raw, raw, normalize=tg, now=NOW)
    assert _count(conn, "delivery_identities") == 3


def test_second_enabled_endpoint_of_one_kind_for_an_identity_is_refused(conn):
    loc = d.add_location(conn, "L", now=NOW)
    r1, r2 = d.add_recipient(conn, now=NOW), d.add_recipient(conn, now=NOW)
    d.add_destination(conn, loc, "telegram", "user:9", "a", normalize=tg, now=NOW)
    with pytest.raises(
        d.DirectoryError, match="^delivery identity already has an enabled endpoint$"
    ):
        d.add_destination(conn, loc, "telegram", "private:9", "b", normalize=tg, now=NOW)
    d.add_contact_point(conn, r1, "whatsapp", PHONE, normalize=wa, now=NOW)
    with pytest.raises(
        d.DirectoryError, match="^delivery identity already has an enabled endpoint$"
    ):
        d.add_contact_point(conn, r2, "whatsapp", "+61-400-000-001", normalize=wa, now=NOW)
    assert not conn.in_transaction


def test_disable_old_create_new_with_the_same_identity_is_allowed(conn):
    r1, r2 = d.add_recipient(conn, now=NOW), d.add_recipient(conn, now=NOW)
    old = d.add_contact_point(conn, r1, "whatsapp", PHONE, normalize=wa, now=NOW)
    d.set_enabled(conn, old, False)
    d.add_contact_point(conn, r2, "whatsapp", PHONE, normalize=wa, now=NOW)
    assert _count(conn, "delivery_identities") == 1 and _count(conn, "contact_points") == 2


def test_changing_an_identity_is_refused(conn):
    rcp = d.add_recipient(conn, now=NOW)
    d.add_contact_point(conn, rcp, "whatsapp", PHONE, normalize=wa, now=NOW)
    with pytest.raises(Exception, match="immutable"):
        conn.execute("UPDATE contact_points SET platform_identity = '+61499999999'")


def test_a_second_enabled_contact_point_per_transport_is_refused(conn):
    rcp = d.add_recipient(conn, now=NOW)
    first = d.add_contact_point(conn, rcp, "whatsapp", PHONE, normalize=wa, now=NOW)
    with pytest.raises(
        d.DirectoryError, match="^recipient already has an enabled contact point on this transport$"
    ):
        d.add_contact_point(conn, rcp, "whatsapp", "+61400000002", normalize=wa, now=NOW)
    d.set_enabled(conn, first, False)
    d.add_contact_point(conn, rcp, "whatsapp", "+61400000002", normalize=wa, now=NOW)


def test_audience_cycle_is_refused(conn):
    a, b, c = (d.add_audience(conn, n, now=NOW) for n in "abc")
    with pytest.raises(d.DirectoryError, match="^audience cycle$"):
        d.add_audience_member(conn, a, a)
    d.add_audience_member(conn, a, b)
    with pytest.raises(d.DirectoryError, match="^audience cycle$"):
        d.add_audience_member(conn, b, a)
    d.add_audience_member(conn, b, c)
    with pytest.raises(d.DirectoryError, match="^audience cycle$"):
        d.add_audience_member(conn, c, a)
    assert _count(conn, "audience_members") == 2


def test_unknown_refs_fail_closed(conn):
    loc = d.add_location(conn, "L", now=NOW)
    rcp = d.add_recipient(conn, now=NOW)
    aud = d.add_audience(conn, "A", now=NOW)
    ghost_loc, ghost_rcp, ghost_aud = "loc_" + "a" * 26, "rcp_" + "a" * 26, "cau_" + "a" * 26
    calls = [
        lambda: d.add_destination(
            conn, ghost_loc, "telegram", "user:1", "x", normalize=tg, now=NOW
        ),
        lambda: d.add_contact_point(conn, ghost_rcp, "whatsapp", PHONE, normalize=wa, now=NOW),
        lambda: d.set_enabled(conn, ghost_loc, False),
        lambda: d.set_enabled(conn, "cmp_" + "a" * 26, False),
        lambda: d.opt_out(conn, "rct_" + "a" * 26, now=NOW),
        lambda: d.add_location_member(conn, loc, ghost_rcp),
        lambda: d.add_audience_member(conn, aud, ghost_aud),
        lambda: d.add_audience_member(conn, ghost_aud, rcp),
        lambda: d.add_audience_member(conn, aud, "not a ref"),
        lambda: d.remove_audience_member(conn, aud, rcp),
        lambda: d.remove_location_member(conn, loc, rcp),
        lambda: d.add_contact_point(conn, rcp, "sms", PHONE, normalize=wa, now=NOW),
        lambda: d.add_destination(conn, loc, "whatsapp", PHONE, "x", normalize=wa, now=NOW),
    ]
    for call in calls:
        with pytest.raises(d.DirectoryError):
            call()
    assert not conn.in_transaction


def test_errors_never_contain_the_identity(conn):
    rcp = d.add_recipient(conn, now=NOW)
    d.add_contact_point(conn, rcp, "whatsapp", PHONE, normalize=wa, now=NOW)
    other = d.add_recipient(conn, now=NOW)
    for call in (
        lambda: d.add_contact_point(conn, other, "whatsapp", PHONE, normalize=wa, now=NOW),
        lambda: d.add_contact_point(conn, rcp, "whatsapp", "+61400000002", normalize=wa, now=NOW),
    ):
        with pytest.raises(d.DirectoryError) as caught:
            call()
        for text in (str(caught.value), repr(caught.value)):
            assert "400000001" not in text and "400000002" not in text
        assert caught.value.__cause__ is None


def test_naive_now_is_refused(conn):
    with pytest.raises(ValueError):
        d.add_location(conn, "L", now=datetime(2026, 9, 24))  # noqa: DTZ001 -- the naive input under test


@pytest.mark.parametrize("seed", range(200))
def test_random_dags_accept_and_every_back_edge_is_refused(conn, seed):
    rng = random.Random(seed)
    n = rng.randint(2, 12)
    nodes = [d.add_audience(conn, str(i), now=NOW) for i in range(n)]
    parents: dict[int, set[int]] = {i: set() for i in range(n)}
    for low in range(n):
        for high in range(low + 1, n):
            if rng.random() < 0.3:
                d.add_audience_member(conn, nodes[low], nodes[high])
                parents[high].add(low)
    candidates = []
    for node in range(n):
        ancestors, stack = set(), list(parents[node])
        while stack:
            up = stack.pop()
            if up not in ancestors:
                ancestors.add(up)
                stack.extend(parents[up])
        candidates += [(node, a) for a in ancestors]
    if candidates:
        node, ancestor = rng.choice(candidates)
        with pytest.raises(d.DirectoryError, match="^audience cycle$"):
            d.add_audience_member(conn, nodes[node], nodes[ancestor])
