"""comms 5b-4 Task 10: the engine — lease, claim, revalidation, the deliver-only exception
boundary, transport isolation, crash seams (design §5.2, §5.3, §10; R5, R9, R18, R19, R20, S7, G4, G9, G13)."""

from datetime import timedelta

import pytest
import sqlcipher3

from comms.core.campaigns import directory as d
from comms.core.delivery import freeze
from comms.core.delivery.engine import CRASH_POINTS, Engine, EngineCrash, ExecutorLease
from comms.core.delivery.reducer import Evidence, reduce
from comms.core.storage.db import open_comms_db, write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, campaign_state, job_states, person, ready

P1, P2, P3 = "+61400000001", "+61400000002", "+61400000003"
BOTH = frozenset({"telegram", "whatsapp"})


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def tx(conn):
    return {"telegram": fakes.FakeTelegram(conn=conn), "whatsapp": fakes.FakeWhatsApp(conn=conn)}


@pytest.fixture
def lease():
    return ExecutorLease(fakes.FakeLock())


def engine(conn, tx, *, crash_at=None, at=NOW):
    return Engine(conn, tx, clock=lambda: at, crash_at=crash_at)


def sent_campaign(conn, tx, targets, transports=frozenset({"whatsapp"})):
    cmp = ready(conn, targets, transports)
    freeze.send(conn, cmp, tx, now=NOW)
    return cmp


def attempts(conn):
    return conn.execute(
        "SELECT attempt_no, finished_at, outcome, provider_message_ref FROM delivery_attempts"
        " ORDER BY id"
    ).fetchall()


def test_engine_crash_escapes_execute(conn, tx, lease):
    rcp, _ = person(conn, phone=P1)
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]})
    with pytest.raises(EngineCrash):
        engine(conn, tx, crash_at="after_claim").execute(lease, cmp)
    assert not issubclass(EngineCrash, Exception)


def test_simulated_crash_from_a_fake_escapes_execute(conn, tx, lease):
    rcp, _ = person(conn, phone=P1)
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]})
    tx["whatsapp"].script[P1] = ["crash_after_accept"]
    with pytest.raises(fakes.SimulatedCrash):
        engine(conn, tx).execute(lease, cmp)
    assert fakes.SimulatedCrash is not EngineCrash
    assert job_states(conn, cmp) == {P1: "IN_FLIGHT"} and tx["whatsapp"].sent == [P1]


def test_happy_path_on_both_transports(conn, tx, lease):
    rcp, _ = person(conn, phone=P1, tg="user:5")
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]}, BOTH)
    report = engine(conn, tx).execute(lease, cmp)
    assert report.claimed == 2 and dict(report.outcomes) == {"ACCEPTED": 2}
    assert campaign_state(conn, cmp) == ("COMPLETE", "SENT")
    assert [a[3] for a in attempts(conn)] == ["telegram-msg-1", "whatsapp-msg-1"]


@pytest.mark.parametrize("failing", ["telegram", "whatsapp"])
def test_a_raising_deliver_on_one_transport_does_not_stop_the_other(conn, tx, lease, failing):
    rcp, _ = person(conn, phone=P1, tg="user:5")
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]}, BOTH)
    tx[failing].script[P1 if failing == "whatsapp" else "5"] = ["raise"]
    engine(conn, tx).execute(lease, cmp)
    other = "whatsapp" if failing == "telegram" else "telegram"
    assert len(tx[other].sent) == 1
    states = job_states(conn, cmp)
    assert states[P1 if failing == "whatsapp" else "5"] == "OUTCOME_UNKNOWN"
    assert campaign_state(conn, cmp) == ("COMPLETE", "INDETERMINATE")


def test_a_raised_deliver_is_outcome_unknown_never_failed(conn, tx, lease):
    rcp, _ = person(conn, phone=P1)
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]})
    tx["whatsapp"].script[P1] = ["raise"]
    engine(conn, tx).execute(lease, cmp)
    assert job_states(conn, cmp) == {P1: "OUTCOME_UNKNOWN"}
    assert attempts(conn)[0][2] == "OUTCOME_UNKNOWN"
    assert campaign_state(conn, cmp)[1] == "INDETERMINATE"


def test_a_core_error_propagates_and_is_never_an_outcome(conn, tx, lease):
    rcp, _ = person(conn, phone=P1, tg="user:5")
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]}, BOTH)
    fakes.plant_failure(conn, "delivery_attempts", "INSERT")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError, match="planted"):
        engine(conn, tx).execute(lease, cmp)
    fakes.clear_planted(conn)
    assert set(job_states(conn, cmp).values()) == {"PENDING"}
    assert tx["telegram"].sent == [] and tx["whatsapp"].sent == []

    def broken(payload, now):
        raise RuntimeError("adapter bug")

    tx["telegram"].still_valid = broken
    with pytest.raises(RuntimeError, match="adapter bug"):
        engine(conn, tx).execute(lease, cmp)
    assert set(job_states(conn, cmp).values()) == {"PENDING"}
    assert [c for c in tx["whatsapp"].calls if c[0] == "deliver"] == []
    assert not conn.in_transaction


def test_revalidation_suppresses_a_member_removed_after_freeze(conn, tx, lease):
    aud = d.add_audience(conn, "A", now=NOW)
    r1, _ = person(conn, phone=P1)
    r2, _ = person(conn, phone=P2)
    for r in (r1, r2):
        d.add_audience_member(conn, aud, r)
    cmp = sent_campaign(conn, tx, {"audiences": [aud]})
    d.remove_audience_member(conn, aud, r2)
    report = engine(conn, tx).execute(lease, cmp)
    assert job_states(conn, cmp) == {P1: "ACCEPTED", P2: "SKIPPED_REVALIDATION"}
    assert report.skipped_revalidation == 1 and tx["whatsapp"].sent == [P1]


def test_revalidation_suppresses_a_direct_destination_whose_location_was_disabled(conn, tx, lease):
    loc = d.add_location(conn, "L", now=NOW)
    dst = d.add_destination(conn, loc, "telegram", "group:1", "g", normalize=fx.tg, now=NOW)
    cmp = sent_campaign(conn, tx, {"destinations": [dst]}, frozenset({"telegram"}))
    d.set_enabled(conn, loc, False)
    engine(conn, tx).execute(lease, cmp)
    assert job_states(conn, cmp) == {"-1": "SKIPPED_REVALIDATION"} and tx["telegram"].sent == []


def test_revalidation_never_adds(conn, tx, lease):
    aud = d.add_audience(conn, "A", now=NOW)
    r1, _ = person(conn, phone=P1)
    d.add_audience_member(conn, aud, r1)
    cmp = sent_campaign(conn, tx, {"audiences": [aud]})
    late, _ = person(conn, phone=P3)
    d.add_audience_member(conn, aud, late)
    engine(conn, tx).execute(lease, cmp)
    assert tx["whatsapp"].sent == [P1] and set(job_states(conn, cmp)) == {P1}


def test_window_closed_between_schedule_and_send_skips(conn, tx, lease):
    rcp, _ = person(conn, phone=P1)
    tx["whatsapp"].script[P1] = [("window_closes_at", NOW + timedelta(hours=1))]
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]})
    engine(conn, tx, at=NOW + timedelta(hours=2)).execute(lease, cmp)
    assert job_states(conn, cmp) == {P1: "SKIPPED_REVALIDATION"} and tx["whatsapp"].sent == []
    assert campaign_state(conn, cmp) == ("COMPLETE", "FAILED")


def test_claim_is_compare_and_set(tmp_path, conn, tx, lease):
    r1, _ = person(conn, phone=P1)
    r2, _ = person(conn, phone=P2)
    cmp = sent_campaign(conn, tx, {"recipients": [r1, r2]})
    other_conn = open_comms_db(tmp_path / "comms.db", fx.KEY)
    other_tx = {"whatsapp": fakes.FakeWhatsApp(conn=other_conn)}
    rival = Engine(other_conn, other_tx, clock=lambda: NOW)
    ran = []

    def interleave(delivery):
        if not ran:  # while the first engine is inside deliver(), a second engine runs
            ran.append(rival.execute(lease, cmp))

    tx["whatsapp"].on_deliver = interleave
    engine(conn, tx).execute(lease, cmp)
    delivered = tx["whatsapp"].sent + other_tx["whatsapp"].sent
    assert sorted(delivered) == [P1, P2]  # each identity exactly once, across both engines
    assert set(job_states(conn, cmp).values()) == {"ACCEPTED"}


def test_cancelled_job_is_not_claimed(conn, tx, lease):
    r1, _ = person(conn, phone=P1)
    r2, _ = person(conn, phone=P2)
    cmp = sent_campaign(conn, tx, {"recipients": [r1, r2]})
    job_id = conn.execute(
        "SELECT j.id FROM delivery_jobs j JOIN delivery_identities i ON i.id = j.identity_id"
        " WHERE i.identity = ?",
        (P2,),
    ).fetchone()[0]
    with write_tx(conn):
        reduce(conn, job_id, None, Evidence.CANCEL, None, now=NOW)
    engine(conn, tx).execute(lease, cmp)
    assert tx["whatsapp"].sent == [P1]


def test_execute_requires_the_lease(conn, tx):
    rcp, _ = person(conn, phone=P1)
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]})
    lock = fakes.FakeLock()
    lock.release()
    with pytest.raises(PermissionError, match="^executor lease required$"):
        engine(conn, tx).execute(ExecutorLease(lock), cmp)
    with pytest.raises(PermissionError):
        engine(conn, tx).execute(object(), cmp)
    assert tx["whatsapp"].sent == []


def test_deliver_is_never_called_inside_a_transaction(conn, tx, lease):
    rcp, _ = person(conn, phone=P1, tg="user:5")
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]}, BOTH)
    engine(conn, tx).execute(lease, cmp)
    delivers = [c for t in tx.values() for c in t.calls if c[0] == "deliver"]
    assert len(delivers) == 2 and not any(c[2] for c in delivers)


def test_missing_transport_fails_before_any_claim(conn, tx, lease):
    rcp, _ = person(conn, phone=P1, tg="user:5")
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]}, BOTH)
    with pytest.raises(Exception, match="transport unavailable"):
        engine(conn, {"whatsapp": tx["whatsapp"]}).execute(lease, cmp)
    assert conn.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_crash_seams_leave_the_section_10_state(conn, tx, lease, point):
    rcp, _ = person(conn, phone=P1)
    cmp = sent_campaign(conn, tx, {"recipients": [rcp]})
    with pytest.raises(EngineCrash):
        engine(conn, tx, crash_at=point).execute(lease, cmp)
    assert job_states(conn, cmp) == {P1: "IN_FLIGHT"}
    assert attempts(conn) == [(1, None, None, None)]  # an open attempt for recovery to close
    assert tx["whatsapp"].sent == ([P1] if point == "after_deliver_before_record" else [])
    assert campaign_state(conn, cmp) == ("SENDING", "IN_PROGRESS")
    assert not conn.in_transaction


def test_an_unknown_crash_point_is_refused(conn, tx):
    with pytest.raises(ValueError):
        Engine(conn, tx, clock=lambda: NOW, crash_at="somewhere")
