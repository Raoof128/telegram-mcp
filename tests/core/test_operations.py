"""comms 5b-4 Task 11: cancel while sending, retry, resolution, provider updates
(design §6.2, §7.2, §7.3; R4, R13, R22, S4, S11, S12, G6, G28)."""

import json

import pytest

from comms.core.campaigns.drafts import LifecycleError
from comms.core.delivery import freeze
from comms.core.delivery import operations as ops
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.delivery.reducer import bind_provider_ref
from comms.core.storage.db import open_comms_db, write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, campaign_state, job_states, person, ready

P1, P2, P3 = "+61400000001", "+61400000002", "+61400000003"


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def tx(conn):
    return {"telegram": fakes.FakeTelegram(conn=conn), "whatsapp": fakes.FakeWhatsApp(conn=conn)}


@pytest.fixture
def lease():
    return ExecutorLease(fakes.FakeLock())


def run(conn, tx, lease, cmp):
    return Engine(conn, tx, clock=lambda: NOW).execute(lease, cmp)


def sent(conn, tx, phones):
    rcps = [person(conn, phone=p)[0] for p in phones]
    cmp = ready(conn, {"recipients": rcps})
    freeze.send(conn, cmp, tx, now=NOW)
    return cmp


def job_ref(conn, phone):
    return conn.execute(
        "SELECT j.ref FROM delivery_jobs j JOIN delivery_identities i ON i.id = j.identity_id"
        " WHERE i.identity = ? ORDER BY j.id DESC",
        (phone,),
    ).fetchone()[0]


def events(conn, kind):
    return [
        json.loads(p)
        for (p,) in conn.execute(
            "SELECT payload FROM campaign_events WHERE event_type = ? ORDER BY event_seq", (kind,)
        )
    ]


def deliveries(fake, phone):
    return sum(1 for c in fake.calls if c[0] == "deliver" and c[1] == phone)


def test_cancel_stops_only_unsent_work(tmp_path, conn, tx, lease):
    cmp = sent(conn, tx, [P1, P2, P3])
    other = open_comms_db(tmp_path / "comms.db", fx.KEY)
    reports = []

    def cancel_while_p1_in_flight(delivery):
        if delivery.identity == P1:
            reports.append(ops.cancel_sending(other, cmp, now=NOW))

    tx["whatsapp"].on_deliver = cancel_while_p1_in_flight
    run(conn, tx, lease, cmp)
    assert reports == [
        ops.CancelReport(cancelled_before_send=2, already_sent=0, currently_in_flight=1)
    ]
    assert job_states(conn, cmp) == {P1: "ACCEPTED", P2: "CANCELLED", P3: "CANCELLED"}
    assert tx["whatsapp"].sent == [P1]
    assert campaign_state(conn, cmp) == ("COMPLETE", "PARTIAL")


def test_cancel_after_some_delivered_ends_partial_not_cancelled(tmp_path, conn, tx, lease):
    cmp = sent(conn, tx, [P1, P2, P3])
    other = open_comms_db(tmp_path / "comms.db", fx.KEY)
    reports = []

    def cancel_while_p2_in_flight(delivery):
        if delivery.identity == P2:
            reports.append(ops.cancel_sending(other, cmp, now=NOW))

    tx["whatsapp"].on_deliver = cancel_while_p2_in_flight
    run(conn, tx, lease, cmp)
    assert reports == [
        ops.CancelReport(cancelled_before_send=1, already_sent=1, currently_in_flight=1)
    ]
    assert campaign_state(conn, cmp) == ("COMPLETE", "PARTIAL")
    assert events(conn, "campaign.cancelled")[-1]["already_sent_count"] == 1


def test_cancel_refuses_outside_sending_and_freeze_cancel_delegates(conn, tx, lease):
    done = sent(conn, tx, [P1])
    run(conn, tx, lease, done)
    with pytest.raises(LifecycleError, match="^campaign is not sending$"):
        ops.cancel_sending(conn, done, now=NOW)
    pending = sent(conn, tx, [P2])
    assert freeze.cancel(conn, pending, now=NOW) == ops.CancelReport(1, 0, 0)
    assert campaign_state(conn, pending) == ("COMPLETE", "CANCELLED")
    assert tx["whatsapp"].sent == [P1]


def test_retry_never_duplicates_successful_delivery(conn, tx, lease):
    cmp = sent(conn, tx, [P1, P2, P3])
    tx["whatsapp"].script.update({P2: ["transient_unsent"], P3: ["permanent"]})
    run(conn, tx, lease, cmp)
    assert ops.retry_failed(conn, cmp, now=NOW) == ops.RetryReport(requeued=1, retry_exhausted=0)
    assert campaign_state(conn, cmp)[0] == "SENDING"
    run(conn, tx, lease, cmp)
    fake = tx["whatsapp"]
    assert (deliveries(fake, P1), deliveries(fake, P2), deliveries(fake, P3)) == (1, 2, 1)
    assert job_states(conn, cmp) == {P1: "ACCEPTED", P2: "ACCEPTED", P3: "FAILED_PERMANENT"}
    assert campaign_state(conn, cmp) == ("COMPLETE", "PARTIAL")
    assert events(conn, "campaign.retry_started")[0]["requeued_count"] == 1


def test_retry_never_touches_outcome_unknown(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    tx["whatsapp"].script[P1] = ["unknown"]
    run(conn, tx, lease, cmp)
    assert ops.retry_failed(conn, cmp, now=NOW) == ops.RetryReport(0, 0)
    assert (
        job_states(conn, cmp) == {P1: "OUTCOME_UNKNOWN"}
        and campaign_state(conn, cmp)[0] == "COMPLETE"
    )


def test_retry_reuses_the_key_and_adds_an_attempt(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    keys = []
    tx["whatsapp"].script[P1] = ["transient_unsent"]
    tx["whatsapp"].on_deliver = lambda d: keys.append((d.idempotency_key, d.attempt_no))
    run(conn, tx, lease, cmp)
    ops.retry_failed(conn, cmp, now=NOW)
    run(conn, tx, lease, cmp)
    assert len(keys) == 1 and keys[0][1] == 2  # the transient attempt never transmitted
    attempts = conn.execute(
        "SELECT attempt_no, outcome FROM delivery_attempts ORDER BY id"
    ).fetchall()
    assert attempts == [(1, "FAILED_TRANSIENT"), (2, "ACCEPTED")]
    assert len({r[0] for r in conn.execute("SELECT idempotency_key FROM delivery_jobs")}) == 1


def test_retry_exhausted_at_the_cap(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    tx["whatsapp"].script[P1] = ["transient_unsent"] * 3
    run(conn, tx, lease, cmp)
    for _ in range(2):
        assert ops.retry_failed(conn, cmp, now=NOW, cap=3).requeued == 1
        run(conn, tx, lease, cmp)
    assert ops.retry_failed(conn, cmp, now=NOW, cap=3) == ops.RetryReport(
        requeued=0, retry_exhausted=1
    )
    assert campaign_state(conn, cmp) == ("COMPLETE", "FAILED")


def test_resolve_sent_and_not_sent(conn, tx, lease):
    cmp = sent(conn, tx, [P1, P2])
    tx["whatsapp"].script.update({P1: ["unknown"], P2: ["unknown"]})
    run(conn, tx, lease, cmp)
    assert campaign_state(conn, cmp) == ("COMPLETE", "INDETERMINATE")
    ops.resolve_outcome(conn, job_ref(conn, P1), "sent", now=NOW)
    ops.resolve_outcome(conn, job_ref(conn, P2), "not_sent", now=NOW)
    assert job_states(conn, cmp) == {P1: "ACCEPTED", P2: "FAILED_TRANSIENT"}
    assert campaign_state(conn, cmp) == ("COMPLETE", "PARTIAL")
    assert [e["verdict"] for e in events(conn, "delivery.outcome_resolved")] == ["sent", "not_sent"]


def test_resolve_refused_unless_outcome_unknown(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    run(conn, tx, lease, cmp)
    with pytest.raises(LifecycleError, match="^job outcome is not unknown$"):
        ops.resolve_outcome(conn, job_ref(conn, P1), "sent", now=NOW)
    with pytest.raises(ValueError):
        ops.resolve_outcome(conn, job_ref(conn, P1), "maybe", now=NOW)
    with pytest.raises(LifecycleError, match="^unknown job$"):
        ops.resolve_outcome(conn, "djb_" + "a" * 26, "sent", now=NOW)


def test_resolve_not_sent_then_retry_makes_a_new_attempt(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    tx["whatsapp"].script[P1] = ["unknown"]
    run(conn, tx, lease, cmp)
    assert ops.retry_failed(conn, cmp, now=NOW).requeued == 0  # unknown is never retried
    ops.resolve_outcome(conn, job_ref(conn, P1), "not_sent", now=NOW)
    assert ops.retry_failed(conn, cmp, now=NOW).requeued == 1
    run(conn, tx, lease, cmp)
    assert deliveries(tx["whatsapp"], P1) == 2 and job_states(conn, cmp) == {P1: "ACCEPTED"}


def update(conn, event_ref, message_ref, status, transport="whatsapp"):
    return ops.record_provider_update(conn, transport, event_ref, message_ref, status, now=NOW)


def test_duplicate_webhook_is_harmless(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    run(conn, tx, lease, cmp)
    assert update(conn, "evt-1", "whatsapp-msg-1", "DELIVERED") == "applied"
    assert update(conn, "evt-1", "whatsapp-msg-1", "DELIVERED") == "duplicate"
    assert job_states(conn, cmp) == {P1: "DELIVERED"}
    assert conn.execute("SELECT count(*) FROM provider_events").fetchone()[0] == 1


def test_duplicate_webhook_with_different_contents_is_a_recorded_conflict(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    run(conn, tx, lease, cmp)
    update(conn, "evt-1", "whatsapp-msg-1", "ACCEPTED")
    assert update(conn, "evt-1", "whatsapp-msg-1", "FAILED_PERMANENT") == "duplicate_conflict"
    assert job_states(conn, cmp) == {P1: "ACCEPTED"}
    assert events(conn, "delivery.provider_update_refused")[-1]["reason"] == "DUPLICATE_CONFLICT"


def test_same_event_ref_on_two_transports_is_two_events(conn, tx, lease):
    rcp, _ = person(conn, phone=P1, tg="user:5")
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"telegram", "whatsapp"}))
    freeze.send(conn, cmp, tx, now=NOW)
    run(conn, tx, lease, cmp)
    assert update(conn, "evt-1", "whatsapp-msg-1", "DELIVERED") == "applied"
    assert update(conn, "evt-1", "telegram-msg-1", "DELIVERED", transport="telegram") == "applied"
    assert set(job_states(conn, cmp).values()) == {"DELIVERED"}


def test_webhook_before_result_is_reconciled_when_the_result_binds(tmp_path, conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    webhook_conn = open_comms_db(tmp_path / "comms.db", fx.KEY)
    seen = []

    def webhook_arrives_first(delivery):
        seen.append(update(webhook_conn, "evt-early", "whatsapp-msg-1", "DELIVERED"))

    tx["whatsapp"].on_deliver = webhook_arrives_first
    run(conn, tx, lease, cmp)
    assert seen == ["pending_match"]
    assert job_states(conn, cmp) == {P1: "DELIVERED"}
    row = conn.execute("SELECT disposition, attempt_id IS NOT NULL FROM provider_events").fetchone()
    assert row == ("applied", 1)
    assert campaign_state(conn, cmp) == ("COMPLETE", "SENT")


def test_ambiguous_provider_reference_is_refused(conn, tx, lease):
    cmp = sent(conn, tx, [P1, P2])
    tx["whatsapp"].script.update({P1: ["unknown"], P2: ["unknown"]})
    run(conn, tx, lease, cmp)
    ids = [r[0] for r in conn.execute("SELECT id FROM delivery_attempts ORDER BY id")]
    conn.execute(
        "UPDATE delivery_attempts SET provider_message_ref = 'dup' WHERE id IN (?, ?)", tuple(ids)
    )
    assert update(conn, "evt-x", "dup", "DELIVERED") == "refused"
    assert set(job_states(conn, cmp).values()) == {"OUTCOME_UNKNOWN"}
    assert events(conn, "delivery.provider_update_refused")[-1]["reason"] == "AMBIGUOUS_MATCH"


def test_ambiguous_binding_refuses_pending_events(conn, tx, lease):
    cmp = sent(conn, tx, [P1, P2])
    tx["whatsapp"].script.update({P1: ["unknown"], P2: ["unknown"]})
    run(conn, tx, lease, cmp)
    first, second = [r[0] for r in conn.execute("SELECT id FROM delivery_attempts ORDER BY id")]
    conn.execute("UPDATE delivery_attempts SET provider_message_ref = 'dup' WHERE id = ?", (first,))
    conn.execute(
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref,"
        " reported_status, disposition, received_at) VALUES ('whatsapp', 'e', 'dup', 'DELIVERED',"
        " 'pending_match', ?)",
        (fx.T0,),
    )  # a state only raw SQL can build
    with write_tx(conn):
        assert bind_provider_ref(conn, second, "dup", now=NOW) == 1
    assert conn.execute("SELECT disposition FROM provider_events").fetchone()[0] == "refused"
    assert job_states(conn, cmp) == {P1: "OUTCOME_UNKNOWN", P2: "OUTCOME_UNKNOWN"}
    assert events(conn, "delivery.provider_update_refused")[-1]["reason"] == "AMBIGUOUS_MATCH"


def test_accepted_then_provider_failure_moves_summary_sent_to_partial_without_touching_lifecycle(
    conn, tx, lease
):
    cmp = sent(conn, tx, [P1, P2])
    run(conn, tx, lease, cmp)
    assert campaign_state(conn, cmp) == ("COMPLETE", "SENT")
    assert update(conn, "evt-f", "whatsapp-msg-2", "FAILED_PERMANENT") == "applied"
    assert campaign_state(conn, cmp) == ("COMPLETE", "PARTIAL")
    assert events(conn, "campaign.summary_changed")[-1] == {"summary": "PARTIAL"}


def test_indeterminate_becomes_sent_on_provider_confirmation(conn, tx, lease):
    cmp = sent(conn, tx, [P1])
    tx["whatsapp"].script[P1] = ["unknown"]
    run(conn, tx, lease, cmp)
    assert campaign_state(conn, cmp) == ("COMPLETE", "INDETERMINATE")
    attempt = conn.execute("SELECT id FROM delivery_attempts").fetchone()[0]
    with write_tx(conn):  # the adapter later learns the provider's reference for this attempt
        bind_provider_ref(conn, attempt, "late-ref", now=NOW)
    assert update(conn, "evt-c", "late-ref", "DELIVERED") == "applied"
    assert campaign_state(conn, cmp) == ("COMPLETE", "SENT")


def test_provider_update_input_is_checked(conn):
    for args in (
        ("sms", "e", "m", "DELIVERED"),
        ("whatsapp", "", "m", "DELIVERED"),
        ("whatsapp", "e", "m", "READ"),
        ("whatsapp", "e", None, "DELIVERED"),
    ):
        with pytest.raises(ValueError):
            ops.record_provider_update(conn, *args, now=NOW)
