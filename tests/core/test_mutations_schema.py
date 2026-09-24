"""comms v0.3 Task D3: schema v4 mutation records, opaque request ids and saga steps (A28, A41,
G14–G16). PREPARED is deliberately not a stored state: a mutation is born IN_FLIGHT."""

import pytest
import sqlcipher3

from comms.core import refs
from comms.core.storage.db import open_comms_db, write_tx
from comms.core.storage.migrations import MIGRATIONS, migrate
from tests.core import schema_fixtures as fx

REQ = "req_" + "a" * 26
STAMP = "2026-09-25T00:00:00.000000Z"
Integrity = sqlcipher3.IntegrityError


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _mutation(conn, request_id=REQ, client="cli_x", state="IN_FLIGHT", **overrides):
    row = {
        "op_ref": refs.mint("operation"),
        "authenticated_client": client,
        "request_id": request_id,
        "request_digest": "d" * 64,
        "tool": "comms_group_member_remove",
        "scope": "provider",
        "target_refs": '["grp_x"]',
        "actor": "telegram_bot",
        "retry_class": "SET_STATE",
        "ambiguity_policy": "retry_same_key",
        "state": state,
        "created_at": STAMP,
        **overrides,
    }
    with write_tx(conn):
        cur = conn.execute(
            f"INSERT INTO mutations ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
    return cur.lastrowid


def _step(conn, mutation_id, step_no=1, state="PENDING"):
    with write_tx(conn):
        conn.execute(
            "INSERT INTO mutation_steps (mutation_id, step_no, capability, state) VALUES (?, ?, 'member.ban', ?)",
            (mutation_id, step_no, state),
        )


def _update(conn, sql, *args):
    with write_tx(conn):
        conn.execute(sql, args)


def test_a_mutation_is_born_in_flight(conn):
    _mutation(conn)
    with pytest.raises(Integrity):
        _mutation(conn, request_id="req_" + "b" * 26, state="PREPARED")


@pytest.mark.parametrize(
    "request_id",
    [
        "remove-jack-from-mq",
        "req_" + "a" * 25,
        "req_" + "a" * 27,
        "req_" + "A" * 26,
        "req_" + "1" * 26,
        "REQ_" + "a" * 26,
        "req_" + "a" * 25 + "!",
    ],
)
def test_request_id_must_be_opaque_req_ref(conn, request_id):
    with pytest.raises(Integrity):
        _mutation(conn, request_id=request_id)


def test_request_ids_are_unique_per_client(conn):
    _mutation(conn)
    with pytest.raises(Integrity):
        _mutation(conn)
    _mutation(conn, client="cli_other")  # another client may use the same id


@pytest.mark.parametrize(
    "column,value",
    [
        ("scope", "global"),
        ("ambiguity_policy", "maybe"),
        ("audit_status", "FINE"),
        ("request_digest", "short"),
    ],
)
def test_closed_columns(conn, column, value):
    with pytest.raises(Integrity):
        _mutation(conn, **{column: value})


@pytest.mark.parametrize(
    "column",
    [
        "op_ref",
        "authenticated_client",
        "request_id",
        "request_digest",
        "tool",
        "scope",
        "target_refs",
        "actor",
        "retry_class",
        "ambiguity_policy",
        "created_at",
    ],
)
def test_binding_columns_are_immutable(conn, column):
    _mutation(conn)
    with pytest.raises(Integrity, match="immutable"):
        _update(conn, f"UPDATE mutations SET {column} = 'x'")


@pytest.mark.parametrize(
    "path,allowed",
    [
        (("SUCCEEDED",), True),
        (("FAILED",), True),
        (("OUTCOME_UNKNOWN", "SUCCEEDED"), True),
        (("OUTCOME_UNKNOWN", "FAILED"), True),
        (("SUCCEEDED", "FAILED"), False),
        (("FAILED", "IN_FLIGHT"), False),
        (("OUTCOME_UNKNOWN", "IN_FLIGHT"), False),
        (("SUCCEEDED", "OUTCOME_UNKNOWN"), False),
    ],
)
def test_state_moves_forward_only(conn, path, allowed):
    _mutation(conn)
    *head, last = path
    for state in head:
        _update(conn, "UPDATE mutations SET state = ?", state)
    if allowed:
        _update(conn, "UPDATE mutations SET state = ?", last)
    else:
        with pytest.raises(Integrity, match="forward"):
            _update(conn, "UPDATE mutations SET state = ?", last)


def test_provider_request_key_is_set_once(conn):
    _mutation(conn)
    _update(conn, "UPDATE mutations SET provider_request_key = 'k1'")
    with pytest.raises(Integrity):
        _update(conn, "UPDATE mutations SET provider_request_key = 'k2'")


def test_steps_forward_only(conn):
    mutation_id = _mutation(conn)
    _step(conn, mutation_id, 1)
    _step(conn, mutation_id, 2)
    _update(conn, "UPDATE mutation_steps SET state = 'IN_FLIGHT' WHERE step_no = 1")
    _update(conn, "UPDATE mutation_steps SET state = 'SUCCEEDED' WHERE step_no = 1")
    for bad in ("PENDING", "IN_FLIGHT", "FAILED"):
        with pytest.raises(Integrity, match="forward"):
            _update(conn, "UPDATE mutation_steps SET state = ? WHERE step_no = 1", bad)
    _update(conn, "UPDATE mutation_steps SET state = 'FAILED' WHERE step_no = 2")  # never attempted
    with pytest.raises(Integrity):
        _step(conn, mutation_id, 2)  # (mutation, step_no) is unique
    with pytest.raises(Integrity):
        _step(conn, mutation_id, 0)
    with pytest.raises(Integrity, match="immutable"):
        _update(conn, "UPDATE mutation_steps SET capability = 'member.unban'")


def test_step_provider_request_key_is_set_once(conn):
    mutation_id = _mutation(conn)
    _step(conn, mutation_id)
    _update(conn, "UPDATE mutation_steps SET provider_request_key = 'k'")
    with pytest.raises(Integrity):
        _update(conn, "UPDATE mutation_steps SET provider_request_key = 'k2'")


def test_mutations_are_never_deleted(conn):
    mutation_id = _mutation(conn)
    _step(conn, mutation_id)
    for table in ("mutation_steps", "mutations"):
        with pytest.raises(Integrity):
            _update(conn, f"DELETE FROM {table}")


def test_v3_rows_survive_v4(tmp_path):
    conn = open_comms_db(tmp_path / "v3.db", fx.KEY)
    migrate(conn, MIGRATIONS[:3])
    fx.world(conn)
    with write_tx(conn):
        conn.execute("INSERT INTO bot_updates VALUES (7, 1, 'message', '{}', ?)", (STAMP,))
        conn.execute(
            "INSERT INTO webhook_inbox (provider_event_ref, received_at, body) VALUES (?, ?, x'00')",
            ("e" * 64, STAMP),
        )
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    columns = {
        t: ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({t})"))
        for t in tables
        if t != "schema_version"
    }
    before = {
        t: conn.execute(f"SELECT {c} FROM {t} ORDER BY 1").fetchall() for t, c in columns.items()
    }
    assert migrate(conn, MIGRATIONS) == 4
    for table, cols in columns.items():  # every v3 column of every v3 row, unchanged
        after = conn.execute(f"SELECT {cols} FROM {table} ORDER BY 1").fetchall()
        assert after == before[table], table
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
