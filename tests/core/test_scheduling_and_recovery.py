"""comms 5b-4 Task 12: run_due with the time gate; recovery; the §10 crash table
(design §8, §10; C6, R7, R16, R20, S9, G18)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
import sqlcipher3

from comms.core.delivery import freeze
from comms.core.delivery import operations as ops
from comms.core.delivery.engine import Engine, EngineCrash, ExecutorLease
from comms.core.delivery.recovery import RecoveryReport, recover
from comms.core.delivery.scheduling import run_due
from comms.core.storage.db import open_comms_db
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, campaign_state, job_states, person, ready

P1, P2 = "+61400000001", "+61400000002"
THURSDAY = datetime(2026, 9, 24, 9, tzinfo=UTC)
FRIDAY = datetime(2026, 9, 25, 8, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def wa(conn):
    return fakes.FakeWhatsApp(conn=conn)


@pytest.fixture
def lease():
    return ExecutorLease(fakes.FakeLock())


def engine(conn, wa, *, at=NOW, crash_at=None):
    return Engine(conn, {"whatsapp": wa}, clock=lambda: at, crash_at=crash_at)


def restart(tmp_path, wa):
    """A new process: a fresh connection to the same file; the fake remembers what it transmitted."""
    new = open_comms_db(tmp_path / "comms.db", fx.KEY)
    wa.conn = new
    return new


def campaign(conn, wa, phones=(P1,)):
    rcps = [person(conn, phone=p)[0] for p in phones]
    return ready(conn, {"recipients": rcps})


def test_scheduled_campaign_needs_no_second_approval(conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.schedule(conn, cmp, FRIDAY, {"whatsapp": wa}, now=NOW)
    assert run_due(lease, conn, engine(conn, wa, at=FRIDAY), now=FRIDAY) == [cmp]
    assert wa.sent == [P1] and campaign_state(conn, cmp) == ("COMPLETE", "SENT")


def test_restart_before_send_at_sends_nothing(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.schedule(conn, cmp, FRIDAY, {"whatsapp": wa}, now=NOW)
    new = restart(tmp_path, wa)
    report = recover(lease, new, now=THURSDAY)
    assert report == RecoveryReport(marked_unknown=0, completed=(), resumable=())
    assert run_due(lease, new, engine(new, wa, at=THURSDAY), now=THURSDAY) == []
    assert Engine(new, {"whatsapp": wa}, clock=lambda: THURSDAY).execute(lease, cmp).claimed == 0
    assert [c for c in wa.calls if c[0] == "deliver"] == []
    assert campaign_state(new, cmp)[0] == "SCHEDULED"


def test_offset_schedule_time_compares_as_utc(conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.schedule(
        conn,
        cmp,
        datetime(2026, 9, 25, 18, tzinfo=timezone(timedelta(hours=10))),
        {"whatsapp": wa},
        now=NOW,
    )
    just_before = FRIDAY - timedelta(microseconds=1)
    assert run_due(lease, conn, engine(conn, wa, at=just_before), now=just_before) == []
    sydney_now = FRIDAY.astimezone(timezone(timedelta(hours=10)))
    assert run_due(lease, conn, engine(conn, wa, at=FRIDAY), now=sydney_now) == [cmp]


def test_recover_never_calls_a_transport_and_reports_resumable(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    new = restart(tmp_path, wa)
    report = recover(lease, new, now=NOW)
    assert report.resumable == (cmp,) and wa.calls == [("prepare", P1, True)]


def test_restart_resumes_unfinished_campaign_safely(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa, (P1, P2))
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    wa.script[P1] = [
        "crash_after_accept"
    ]  # P1 transmitted, then the process died; P2 never claimed
    with pytest.raises(fakes.SimulatedCrash):
        engine(conn, wa).execute(lease, cmp)
    new = restart(tmp_path, wa)
    report = recover(lease, new, now=NOW)
    assert report.marked_unknown == 1 and report.resumable == (cmp,)
    assert job_states(new, cmp) == {P1: "OUTCOME_UNKNOWN", P2: "PENDING"}
    for ref in report.resumable:
        engine(new, wa).execute(lease, ref)
    assert wa.sent == [P1, P2]  # P2 resumed; P1 never resent
    assert campaign_state(new, cmp) == ("COMPLETE", "INDETERMINATE")


def test_delivery_idempotency_survives_restart(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa, (P1, P2))
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    keys = []
    wa.on_deliver = lambda d: keys.append(d.idempotency_key)
    engine(conn, wa).execute(lease, cmp)
    new = restart(tmp_path, wa)
    recover(lease, new, now=NOW)
    engine(new, wa).execute(lease, cmp)
    stored = sorted(r[0] for r in new.execute("SELECT idempotency_key FROM delivery_jobs"))
    assert sorted(keys) == stored and wa.sent == [P1, P2]


# --- the §10 crash table, one test per row -------------------------------------------


def test_crash_before_the_freeze_commits(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    fakes.plant_failure(conn, "campaign_events", "INSERT")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
        freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    new = restart(tmp_path, wa)
    assert recover(lease, new, now=NOW) == RecoveryReport(0, (), ())
    assert new.execute("SELECT count(*) FROM delivery_jobs").fetchone()[0] == 0
    assert campaign_state(new, cmp) == ("READY", None) and wa.sent == []


def test_crash_after_freeze_before_claim(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    new = restart(tmp_path, wa)
    assert recover(lease, new, now=NOW).resumable == (cmp,)
    assert job_states(new, cmp) == {P1: "PENDING"}
    engine(new, wa).execute(lease, cmp)
    assert wa.sent == [P1]


@pytest.mark.parametrize("point", ["after_claim", "during_deliver"])
def test_crash_after_claim_or_during_deliver_is_outcome_unknown(tmp_path, conn, wa, lease, point):
    cmp = campaign(conn, wa)
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    with pytest.raises(EngineCrash):
        engine(conn, wa, crash_at=point).execute(lease, cmp)
    new = restart(tmp_path, wa)
    assert recover(lease, new, now=NOW) == RecoveryReport(
        marked_unknown=1, completed=(cmp,), resumable=()
    )
    assert job_states(new, cmp) == {P1: "OUTCOME_UNKNOWN"}
    engine(new, wa).execute(lease, cmp)
    assert [c for c in wa.calls if c[0] == "deliver"] == []


def test_provider_accepted_before_the_outcome_commits_is_never_resent(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    wa.script[P1] = ["crash_after_accept"]
    with pytest.raises(fakes.SimulatedCrash):
        engine(conn, wa).execute(lease, cmp)
    assert wa.sent == [P1]
    new = restart(tmp_path, wa)
    recover(lease, new, now=NOW)
    assert ops.retry_failed(new, cmp, now=NOW).requeued == 0
    engine(new, wa).execute(lease, cmp)
    assert wa.sent == [P1]  # exactly one transmission, ever
    assert job_states(new, cmp) == {P1: "OUTCOME_UNKNOWN"}


def test_crash_after_the_last_outcome_commits(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    engine(conn, wa).execute(lease, cmp)
    new = restart(tmp_path, wa)
    assert recover(lease, new, now=NOW) == RecoveryReport(0, (), ())
    assert campaign_state(new, cmp) == ("COMPLETE", "SENT")


def test_the_stranded_sending_state_is_completed(conn, lease):
    w = fx.world(conn)
    conn.execute("UPDATE delivery_jobs SET state = 'ACCEPTED', attempt_count = 1")
    fx.attempt(conn, w["job"], outcome="ACCEPTED")
    report = recover(lease, conn, now=NOW)
    assert report.completed == (w["campaign_ref"],)
    assert conn.execute("SELECT lifecycle, summary FROM campaigns").fetchone() == (
        "COMPLETE",
        "SENT",
    )


def test_recover_closes_the_open_attempt(tmp_path, conn, wa, lease):
    cmp = campaign(conn, wa)
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    with pytest.raises(EngineCrash):
        engine(conn, wa, crash_at="after_claim").execute(lease, cmp)
    new = restart(tmp_path, wa)
    recover(lease, new, now=NOW + timedelta(minutes=5))
    row = new.execute("SELECT outcome, finished_at FROM delivery_attempts").fetchone()
    assert row == ("OUTCOME_UNKNOWN", "2026-09-24T00:05:00.000000Z")


def test_operator_operations_are_all_or_nothing(conn, wa, lease):
    scheduled = campaign(conn, wa)
    freeze.schedule(conn, scheduled, FRIDAY, {"whatsapp": wa}, now=NOW)
    sending = campaign(conn, wa, (P2,))
    freeze.send(conn, sending, {"whatsapp": wa}, now=NOW)
    wa.script[P2] = ["unknown"]
    engine(conn, wa).execute(lease, sending)
    job = conn.execute("SELECT ref FROM delivery_jobs WHERE state = 'OUTCOME_UNKNOWN'").fetchone()[
        0
    ]
    before = conn.execute("SELECT group_concat(state) FROM delivery_jobs").fetchone()[0]
    fakes.plant_failure(conn, "campaign_events", "INSERT")
    for call in (
        lambda: freeze.unschedule(conn, scheduled, now=NOW),
        lambda: freeze.cancel(conn, scheduled, now=NOW),
        lambda: ops.retry_failed(conn, sending, now=NOW),
        lambda: ops.resolve_outcome(conn, job, "not_sent", now=NOW),
    ):
        with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
            call()
    fakes.clear_planted(conn)
    assert conn.execute("SELECT group_concat(state) FROM delivery_jobs").fetchone()[0] == before
    assert campaign_state(conn, scheduled)[0] == "SCHEDULED"
    assert (
        conn.execute("SELECT count(*) FROM generations WHERE status = 'discarded'").fetchone()[0]
        == 0
    )


def test_recover_and_run_due_require_the_lease(conn, wa):
    lock = fakes.FakeLock()
    lock.release()
    with pytest.raises(PermissionError):
        recover(ExecutorLease(lock), conn, now=NOW)
    with pytest.raises(PermissionError):
        run_due(ExecutorLease(lock), conn, engine(conn, wa), now=NOW)


def test_run_due_requires_a_timezone(conn, wa, lease):
    with pytest.raises(ValueError):
        run_due(lease, conn, engine(conn, wa), now=datetime(2026, 9, 25))  # noqa: DTZ001
