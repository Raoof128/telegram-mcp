"""comms v0.3 Task D1: grp_ ↔ dst_, one to one, groups only (design D.4)."""

import pytest
import sqlcipher3

from comms.core import refs
from comms.core.campaigns import directory as d
from comms.core.groups import GroupError, destination_of, group_ref
from comms.core.storage.db import write_tx
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _destination(conn, identity):
    loc = d.add_location(conn, "MQ", now=NOW)
    return d.add_destination(conn, loc, "telegram", identity, "g", normalize=fx.tg, now=NOW)


@pytest.mark.parametrize("identity", ["group:55", "channel:1234567890"])
def test_group_maps_one_to_one_to_a_group_destination(conn, identity):
    dst = _destination(conn, identity)
    grp = group_ref(conn, dst, now=NOW)
    refs.check(grp, "group")
    assert group_ref(conn, dst, now=NOW) == grp  # stable: get, never a second mint
    assert destination_of(conn, grp) == dst
    assert conn.execute("SELECT count(*) FROM groups").fetchone()[0] == 1


@pytest.mark.parametrize("identity", ["user:42", "private:42"])
def test_private_chat_destination_cannot_be_a_group(conn, identity):
    dst = _destination(conn, identity)
    with pytest.raises(GroupError):
        group_ref(conn, dst, now=NOW)
    destination_id = conn.execute("SELECT id FROM destinations WHERE ref = ?", (dst,)).fetchone()[0]
    with pytest.raises(sqlcipher3.IntegrityError, match="not a group"), write_tx(conn):
        conn.execute(
            "INSERT INTO groups (ref, destination_id, created_at) VALUES (?, ?, ?)",
            (refs.mint("group"), destination_id, "2026-09-25T00:00:00.000000Z"),
        )


def test_the_mapping_is_database_enforced(conn):
    dst = _destination(conn, "group:55")
    group_ref(conn, dst, now=NOW)
    destination_id = conn.execute("SELECT id FROM destinations WHERE ref = ?", (dst,)).fetchone()[0]
    with pytest.raises(sqlcipher3.IntegrityError), write_tx(conn):
        conn.execute(
            "INSERT INTO groups (ref, destination_id, created_at) VALUES (?, ?, ?)",
            (refs.mint("group"), destination_id, "2026-09-25T00:00:00.000000Z"),
        )
    with pytest.raises(sqlcipher3.IntegrityError), write_tx(conn):
        conn.execute("UPDATE groups SET destination_id = destination_id + 1")


@pytest.mark.parametrize("value", ["dst_nope", "grp_" + "a" * 26, "", "cmp_" + "a" * 26])
def test_unknown_or_wrong_refs_are_refused(conn, value):
    with pytest.raises(GroupError):
        destination_of(conn, value) if value.startswith("grp_") else group_ref(conn, value, now=NOW)


def test_a_group_destination_is_listed_from_the_moment_it_exists(tmp_path):
    """D39-PRE E11b found: a group got its grp_ ref only when resolved by name, so a group added
    any other way (a backup restore, the directory) was invisible to every MCP tool."""
    from comms.core.campaigns import directory as d
    from comms.core.groups import list_groups
    from tests.core import schema_fixtures as fx
    from tests.core.campaign_helpers import NOW

    conn = fx.migrated(tmp_path)
    loc = d.add_location(conn, "MQ", now=NOW)
    d.add_destination(
        conn, loc, "telegram", "channel:1234567890", "MQ Society", normalize=fx.tg, now=NOW
    )
    d.add_destination(
        conn, loc, "telegram", "user:42", "Ali DM", normalize=fx.tg, now=NOW
    )  # not a group
    items, _more = list_groups(conn, limit=10)
    assert [i["name"] for i in items] == ["MQ Society"]
