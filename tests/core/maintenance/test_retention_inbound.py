"""comms v0.3 (Part C exit follow-up, A15): retained inbound bodies — Bot API updates, MTProto
updates and completed webhook bodies — follow the body-retention window."""

from datetime import timedelta

import pytest

from comms.core.audit import cutover
from comms.core.maintenance.retention import (
    CRASH_POINTS,
    RetentionCrash,
    RetentionPolicy,
    purge_inbound,
    run_retention,
)
from comms.core.storage.db import write_tx
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.core.audit.legacy_fixtures import CHAIN_KEY, comms_world, public_for
from tests.core.campaign_helpers import NOW

OLD = "2026-08-01T00:00:00.000000Z"
RECENT = "2026-09-23T00:00:00.000000Z"


def _seed(conn):
    with write_tx(conn):
        for update_id, at in ((1, OLD), (2, RECENT)):
            conn.execute(
                "INSERT INTO bot_updates (update_id, chat_id, kind, payload, received_at) VALUES (?, 1, 'message', '{}', ?)",
                (update_id, at),
            )
        for ref, at in (("-1:1", OLD), ("-1:2", RECENT)):
            conn.execute(
                "INSERT INTO user_updates (event_ref, chat_id, message_id, kind, payload, received_at)"
                " VALUES (?, '-1', 1, 'message', '{}', ?)",
                (ref, at),
            )
        for ref, done in (("a" * 64, OLD), ("b" * 64, RECENT), ("c" * 64, None)):
            conn.execute(
                "INSERT INTO webhook_inbox (provider_event_ref, received_at, body, archive_done, window_done,"
                " status_done, completed_at) VALUES (?, ?, x'00', ?, ?, ?, ?)",
                (
                    ref,
                    OLD,
                    int(done is not None),
                    int(done is not None),
                    int(done is not None),
                    done,
                ),
            )


@pytest.fixture
def world(tmp_path):
    world = comms_world(tmp_path)
    _seed(world["conn"])
    return world


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_bodies_older_than_the_window_go_and_incomplete_inbox_rows_stay(world):
    conn = world["conn"]
    with write_tx(conn):
        removed = purge_inbound(conn, cutoff=NOW - timedelta(days=30))
    assert removed == 3
    assert (_count(conn, "bot_updates"), _count(conn, "user_updates")) == (1, 1)
    refs = {r[0][0] for r in conn.execute("SELECT provider_event_ref FROM webhook_inbox")}
    assert refs == {"b", "c"}  # the recent one, and the unfinished one whatever its age


def test_the_retention_run_reports_the_phase(world):
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    legacy = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    policy = RetentionPolicy(3650, 3650, 3650, 3650, 30, 3650)
    report = run_retention(
        world["conn"], legacy, policy, world["writer"], now=NOW, store=world["store"]
    )
    assert report.phases["inbound_bodies"] == 3
    assert "after_inbound" in CRASH_POINTS


def test_a_crash_after_the_phase_keeps_it_done_and_a_rerun_converges(world):
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    legacy = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    policy = RetentionPolicy(3650, 3650, 3650, 3650, 30, 3650)
    with pytest.raises(RetentionCrash):
        run_retention(
            world["conn"],
            legacy,
            policy,
            world["writer"],
            now=NOW,
            store=world["store"],
            crash_at="after_inbound",
        )
    assert _count(world["conn"], "bot_updates") == 1
    report = run_retention(
        world["conn"], legacy, policy, world["writer"], now=NOW, store=world["store"]
    )
    assert report.phases["inbound_bodies"] == 0
