"""comms v0.3 Task A10: the cutover's legacy side — barrier, drain, verify, seal, anchor (A6, G3)."""

import sqlite3

import pytest

from comms.core.audit import cutover as co
from comms.core.audit.chain import append_event as core_append
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit.chain import append_event, head
from comms.transports.telegram.disclosure.audit.profile import LEGACY_TELEGRAM
from tests.core import schema_fixtures as fx
from tests.core.audit.legacy_fixtures import CHAIN_KEY, legacy_event, legacy_port
from tests.core.campaign_helpers import NOW


@pytest.fixture
def comms(tmp_path):
    return fx.migrated(tmp_path)


def _state(conn):
    return conn.execute(
        "SELECT phase, cutover_ref, legacy_checkpoint_digest FROM cutover_state"
    ).fetchone()


def test_phases_advance_in_order_and_persist(tmp_path, comms):
    port = legacy_port(tmp_path)
    assert co.advance_legacy(comms, port, now=NOW) == "LEGACY_ANCHORED"
    phase, cut, digest = _state(comms)
    assert phase == "LEGACY_ANCHORED" and cut.startswith("cut_") and len(digest) == 64
    assert port.sealed_record().checkpoint_digest == digest
    assert port.gate.closed


def test_drain_timeout_refuses_and_stays_at_cutover_entered(tmp_path, comms):
    port = legacy_port(tmp_path)
    port.gate.enter()  # a legacy request still in flight
    with pytest.raises(co.CutoverError, match="did not drain"):
        co.advance_legacy(comms, port, now=NOW, drain_timeout_s=0.05)
    assert _state(comms)[0] == "CUTOVER_ENTERED"
    port.gate.exit()
    assert co.advance_legacy(comms, port, now=NOW) == "LEGACY_ANCHORED"


def test_unverifiable_legacy_chain_refuses_before_sealing(tmp_path, comms):
    port = legacy_port(tmp_path)
    port.conn.execute("UPDATE audit_events SET status = 'error' WHERE chain_seq = 2")
    port.conn.commit()
    with pytest.raises(co.CutoverError, match="legacy chain did not verify"):
        co.advance_legacy(comms, port, now=NOW)
    assert _state(comms)[0] == "LEGACY_DRAINED" and port.sealed_record() is None


def test_after_seal_a_legacy_append_is_structurally_impossible(tmp_path, comms):
    port = legacy_port(tmp_path)
    co.advance_legacy(comms, port, now=NOW)
    with pytest.raises(sqlite3.IntegrityError, match="legacy chain is sealed"), write_tx(port.conn):
        append_event(port.conn, CHAIN_KEY, legacy_event())
    with pytest.raises(sqlite3.IntegrityError, match="legacy chain is sealed"), write_tx(port.conn):
        core_append(
            port.conn, LEGACY_TELEGRAM, CHAIN_KEY, legacy_event()
        )  # the core engine, directly
    with pytest.raises(sqlite3.IntegrityError, match="sealed"):
        port.conn.execute(
            "UPDATE settings SET value_json = '\"open\"' WHERE key = 'audit.append_state'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="sealed"):
        port.conn.execute("DELETE FROM settings WHERE key = 'audit.append_state'")


def test_the_marker_and_checkpoint_are_one_transaction(tmp_path, comms):
    port = legacy_port(tmp_path)
    port.conn.execute(
        "CREATE TEMP TRIGGER planted BEFORE INSERT ON main.audit_checkpoints"
        " BEGIN SELECT RAISE(ABORT, 'planted'); END"
    )
    before = head(port.conn)
    with pytest.raises(sqlite3.IntegrityError, match="planted"):
        co.advance_legacy(comms, port, now=NOW)
    assert head(port.conn) == before and port.sealed_record() is None
    assert _state(comms)[0] == "LEGACY_VERIFIED"


@pytest.mark.parametrize(
    "point",
    [
        "after_CUTOVER_ENTERED",
        "after_LEGACY_DRAINED",
        "after_LEGACY_VERIFIED",
        "after_legacy_seal_commit",
        "after_LEGACY_SEALED",
        "after_LEGACY_ANCHORED",
    ],
)
def test_replay_at_each_phase_reuses_the_cut_ref_and_never_writes_a_second_seal(
    tmp_path, comms, point
):
    port = legacy_port(tmp_path)
    with pytest.raises(co.CutoverCrash):
        co.advance_legacy(comms, port, now=NOW, crash_at=point)
    first = _state(comms)[1]
    assert co.advance_legacy(comms, port, now=NOW) == "LEGACY_ANCHORED"
    assert _state(comms)[1] == first
    markers = port.conn.execute(
        "SELECT count(*) FROM audit_events WHERE tool_name = 'system.cutover_final'"
    )
    assert markers.fetchone()[0] == 1
    assert port.conn.execute("SELECT count(*) FROM audit_checkpoints").fetchone()[0] == 1


def test_a_conflicting_legacy_seal_fails_closed(tmp_path, comms):
    port = legacy_port(tmp_path)
    co.advance_legacy(comms, port, now=NOW)
    other = legacy_port(tmp_path, name="other.db")
    other.seal(now=NOW)
    comms_phase_back = comms.execute(
        "SELECT legacy_checkpoint_digest FROM cutover_state"
    ).fetchone()[0]
    assert comms_phase_back != other.sealed_record().checkpoint_digest
    with pytest.raises(co.CutoverError, match="lineage conflict"):
        co.record_seal(comms, other.sealed_record(), now=NOW)
