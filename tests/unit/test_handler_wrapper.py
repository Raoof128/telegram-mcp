"""The only place an admin transaction commits (design §2.1, 0B G4, G5, G12)."""

import os

import pytest

from comms.transports.telegram.disclosure.audit.anchor import CLEAN, derive_integrity
from comms.transports.telegram.disclosure.audit.chain import verify_chain
from comms.transports.telegram.ipc.handlers._wrapper import (
    AuditSink,
    TxCommand,
    admin_event,
    run_audited_tx,
    run_tx,
    simulate_tx,
)
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.settings import get_setting
from tests.authority_fixtures import seed_authority_rows

KEY = b"k" * 32
NOW = "2026-09-24T00:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    return c


def _rename(name="Renamed", seen=None):
    def plan(conn, parsed):
        if seen is not None:
            seen.append(conn.in_transaction)
        return parsed

    def apply(conn, plan):
        conn.execute("UPDATE projects SET display_name = ?", (plan["name"],))
        return {"name": plan["name"]}

    return TxCommand(parse=lambda args: {"name": args.get("name", name)}, plan=plan, apply=apply)


def _name(conn):
    return conn.execute("SELECT display_name FROM projects").fetchone()[0]


def test_plan_runs_inside_the_transaction_and_run_tx_commits(conn):
    seen: list[bool] = []
    assert run_tx(conn, _rename(seen=seen), {}) == {"name": "Renamed"}
    assert seen == [True]
    assert _name(conn) == "Renamed" and not conn.in_transaction


def test_presence_never_reaches_parse(conn):
    got = {}
    cmd = TxCommand(
        parse=lambda args: got.update(args) or {}, plan=lambda c, p: p, apply=lambda c, p: {}
    )
    run_tx(conn, cmd, {"presence": {"token": "t"}, "x": 1})
    assert got == {"x": 1}


def test_an_apply_failure_rolls_everything_back(conn):
    def apply(conn, plan):
        conn.execute("UPDATE projects SET display_name = 'half'")
        raise ValueError("refused")

    with pytest.raises(ValueError, match="refused"):
        run_tx(conn, TxCommand(lambda a: {}, lambda c, p: p, apply), {})
    assert _name(conn) == "Alpha" and not conn.in_transaction


def test_a_post_commit_failure_never_undoes_the_commit(conn):
    def boom(result):
        raise RuntimeError("cache eviction failed")

    cmd = _rename()
    cmd = TxCommand(cmd.parse, cmd.plan, cmd.apply, post_commit=boom)
    assert run_tx(conn, cmd, {}) == {"name": "Renamed"}
    assert _name(conn) == "Renamed"


def test_simulate_observes_the_change_and_leaves_no_trace(conn):
    before, after = simulate_tx(conn, _rename(), {}, _name)
    assert (before, after) == ("Alpha", "Renamed")
    assert _name(conn) == "Alpha"


def test_simulate_never_leaves_a_transaction_open(conn):
    def apply(conn, plan):
        raise ValueError("refused")

    with pytest.raises(ValueError):
        simulate_tx(conn, TxCommand(lambda a: {}, lambda c, p: p, apply), {}, _name)
    assert not conn.in_transaction
    assert run_tx(conn, _rename(), {}) == {"name": "Renamed"}  # connection still usable


@pytest.fixture
def sink(tmp_path):
    anchor_dir = tmp_path / "anchor"
    anchor_dir.mkdir(mode=0o700)
    return AuditSink(chain_key=KEY, anchor_path=anchor_dir / "anchor.json", now=lambda: NOW)


def test_an_audited_command_appends_and_refreshes_the_anchor(conn, sink):
    result = run_audited_tx(
        conn, sink, _rename(), {}, event=lambda plan, result: admin_event("admin.lock", NOW)
    )
    assert result == {"name": "Renamed", "anchor": "refreshed"}
    verify_chain(conn, KEY)
    assert derive_integrity(conn, KEY, sink.anchor_path) == CLEAN


def test_an_anchor_failure_keeps_the_commit_and_latches_degraded(conn, tmp_path):
    broken = AuditSink(KEY, tmp_path / "missing-dir" / "anchor.json", lambda: NOW)
    result = run_audited_tx(
        conn, broken, _rename(), {}, event=lambda p, r: admin_event("admin.lock", NOW)
    )
    assert result["anchor"] == "degraded" and _name(conn) == "Renamed"
    assert get_setting(conn, "audit.integrity_degraded") == 1


def test_audited_commands_refuse_while_degraded(conn, sink):
    from comms.transports.telegram.disclosure.audit.anchor import latch_degraded

    latch_degraded(conn, reason="anchor_refresh_failure")
    with pytest.raises(PermissionError):
        run_audited_tx(conn, sink, _rename(), {}, event=lambda p, r: admin_event("admin.lock", NOW))
    assert _name(conn) == "Alpha"


def test_a_busy_database_is_a_fixed_refusal(conn, tmp_path):
    from comms.transports.telegram.ipc.handlers._wrapper import BUSY

    holder = open_db(tmp_path / "m.db")
    holder.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA busy_timeout = 50")
    try:
        with pytest.raises(ValueError, match=BUSY):
            run_tx(conn, _rename(), {})
        with pytest.raises(ValueError, match=BUSY):
            simulate_tx(conn, _rename(), {}, _name)
        assert not conn.in_transaction
    finally:
        holder.rollback()
    assert run_tx(conn, _rename(), {}) == {"name": "Renamed"}


def test_admin_event_has_every_column_and_a_closed_tool_name():
    from comms.transports.telegram.disclosure.audit.chain import EVENT_COLUMNS

    event = admin_event("admin.unlock", NOW)
    assert set(event) == set(EVENT_COLUMNS) and event["status"] == "ok"
    with pytest.raises(ValueError):
        admin_event("telegram_status", NOW)


def test_the_anchor_directory_mode_is_what_read_anchor_demands(sink):
    assert oct(os.stat(sink.anchor_path.parent).st_mode & 0o777) == "0o700"
