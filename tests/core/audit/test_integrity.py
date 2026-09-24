"""comms v0.3 Task A9: the degraded latch blocks new effects, never the recording of started ones (A8)."""

from datetime import timedelta

import pytest

from comms.core.audit.integrity import (
    AuditIntegrityDegraded,
    clear_degraded,
    is_degraded,
    latch_degraded,
    require_not_degraded,
)
from comms.core.delivery import freeze
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.delivery.recovery import recover
from comms.core.delivery.reducer import Evidence, reduce
from comms.core.delivery.scheduling import run_due
from comms.core.storage.db import write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, campaign_state, job_states, person, ready

P1 = "+61400000001"


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def test_latch_is_idempotent_and_keeps_the_first_reason(conn):
    assert not is_degraded(conn)
    latch_degraded(conn, reason="FIRST", now=NOW)
    latch_degraded(conn, reason="SECOND", now=NOW + timedelta(seconds=5))
    assert conn.execute("SELECT state, reason, since FROM audit_integrity").fetchone() == (
        "degraded",
        "FIRST",
        fx.T0,
    )
    with pytest.raises(AuditIntegrityDegraded, match="^AUDIT_INTEGRITY_DEGRADED$"):
        require_not_degraded(conn)
    clear_degraded(conn, now=NOW)
    require_not_degraded(conn)
    assert conn.execute("SELECT state, reason, since FROM audit_integrity").fetchone() == (
        "ok",
        None,
        None,
    )


def test_claim_is_refused_while_degraded(conn):
    w = fx.world(conn)
    latch_degraded(conn, reason="X", now=NOW)
    with write_tx(conn):
        assert reduce(conn, w["job"], None, Evidence.CLAIM, None, now=NOW).disposition == "refused"
    assert conn.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 0


def test_execute_and_run_due_raise_before_any_claim(conn):
    wa = fakes.FakeWhatsApp(conn=conn)
    rcp, _ = person(conn, phone=P1)
    cmp = ready(conn, {"recipients": [rcp]})
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    scheduled = ready(conn, {"recipients": [rcp]})
    freeze.schedule(conn, scheduled, NOW, {"whatsapp": wa}, now=NOW)
    latch_degraded(conn, reason="X", now=NOW)
    lease = ExecutorLease(fakes.FakeLock())
    engine = Engine(conn, {"whatsapp": wa}, clock=lambda: NOW)
    with pytest.raises(AuditIntegrityDegraded):
        engine.execute(lease, cmp)
    with pytest.raises(AuditIntegrityDegraded):
        run_due(lease, conn, engine, now=NOW)
    assert campaign_state(conn, scheduled)[0] == "SCHEDULED"
    assert [c for c in wa.calls if c[0] == "deliver"] == []


def test_recording_an_in_flight_result_and_recovery_are_allowed_while_degraded(conn):
    w = fx.world(conn)
    with write_tx(conn):
        t = reduce(conn, w["job"], None, Evidence.CLAIM, None, now=NOW)
    latch_degraded(conn, reason="X", now=NOW)
    with write_tx(conn):
        assert (
            reduce(conn, w["job"], t.attempt_id, Evidence.RESULT, "ACCEPTED", now=NOW).disposition
            == "applied"
        )
    lease = ExecutorLease(fakes.FakeLock())
    recover(lease, conn, now=NOW)
    assert campaign_state(conn, w["campaign_ref"]) == ("COMPLETE", "SENT")


def test_recover_marks_in_flight_unknown_while_degraded(conn):
    w = fx.world(conn)
    with write_tx(conn):
        reduce(conn, w["job"], None, Evidence.CLAIM, None, now=NOW)
    latch_degraded(conn, reason="X", now=NOW)
    report = recover(ExecutorLease(fakes.FakeLock()), conn, now=NOW)
    assert report.marked_unknown == 1
    assert list(job_states(conn, w["campaign_ref"]).values()) == ["OUTCOME_UNKNOWN"]


def test_reads_are_unaffected(conn):
    w = fx.world(conn)
    latch_degraded(conn, reason="X", now=NOW)
    assert job_states(conn, w["campaign_ref"])
