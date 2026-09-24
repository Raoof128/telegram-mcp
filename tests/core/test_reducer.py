"""comms 5b-4 Task 8: one reducer for every job-state write; the summary derived with it
(design §6.3, §6.4, §7.3, R4, R7, R8, G3, G6, F4, F5)."""

import itertools
import json
from datetime import UTC, datetime

import pytest
import sqlcipher3

from comms.core.delivery import reducer as r
from comms.core.delivery.reducer import Evidence as E
from comms.core.storage.db import write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)
STATES = [
    "PENDING",
    "IN_FLIGHT",
    "ACCEPTED",
    "DELIVERED",
    "FAILED_TRANSIENT",
    "FAILED_PERMANENT",
    "OUTCOME_UNKNOWN",
    "CANCELLED",
    "SKIPPED_PLATFORM_POLICY",
    "SKIPPED_REVALIDATION",
]
KINDS = ["ACCEPTED", "DELIVERED", "FAILED_TRANSIENT", "FAILED_PERMANENT", "OUTCOME_UNKNOWN"]
PROVIDER = ["ACCEPTED", "DELIVERED", "FAILED_PERMANENT"]
LOW = ["PENDING", "IN_FLIGHT", "FAILED_TRANSIENT", "OUTCOME_UNKNOWN"]

# The expected table, written as data (not recomputed): every APPLIED transition.
# Key: (state, evidence, value, is_current_attempt) → new state. BOTH = either flag.
BOTH = (True, False)
APPLIED: dict[tuple[str, str, str | None, bool], str] = {}
for cur in BOTH:
    APPLIED[("PENDING", "CLAIM", None, cur)] = "IN_FLIGHT"
    APPLIED[("PENDING", "CANCEL", None, cur)] = "CANCELLED"
    APPLIED[("PENDING", "SKIP_REVALIDATION", None, cur)] = "SKIPPED_REVALIDATION"
    APPLIED[("FAILED_TRANSIENT", "RETRY", None, cur)] = "PENDING"
    APPLIED[("OUTCOME_UNKNOWN", "RESOLVE_SENT", None, cur)] = "ACCEPTED"
    APPLIED[("OUTCOME_UNKNOWN", "RESOLVE_NOT_SENT", None, cur)] = "FAILED_TRANSIENT"
    for s in LOW:
        APPLIED[(s, "PROVIDER", "ACCEPTED", cur)] = "ACCEPTED"
    for s in [*LOW, "ACCEPTED"]:
        APPLIED[(s, "PROVIDER", "DELIVERED", cur)] = "DELIVERED"
APPLIED[("IN_FLIGHT", "RECOVER", None, True)] = "OUTCOME_UNKNOWN"
for k in KINDS:
    APPLIED[("IN_FLIGHT", "RESULT", k, True)] = k
for s in ("IN_FLIGHT", "ACCEPTED", "OUTCOME_UNKNOWN"):
    APPLIED[(s, "PROVIDER", "FAILED_PERMANENT", True)] = "FAILED_PERMANENT"
# Evidence the reducer keeps on the attempt without moving the job.
RECORDED = {("ACCEPTED", "RESULT", k, True) for k in KINDS} | {
    ("DELIVERED", "RESULT", k, True) for k in KINDS
}

VALUES = {
    "CLAIM": [None],
    "RESULT": KINDS,
    "PROVIDER": PROVIDER,
    "RESOLVE_SENT": [None],
    "RESOLVE_NOT_SENT": [None],
    "CANCEL": [None],
    "SKIP_REVALIDATION": [None],
    "RETRY": [None],
    "RECOVER": [None],
}
CASES = [(s, e, v, c) for s in STATES for e, vs in VALUES.items() for v in vs for c in BOTH]


def test_the_case_space_is_the_full_product():
    assert len(CASES) == 10 * 15 * 2 and {e.value for e in E} == set(VALUES)


@pytest.mark.parametrize(("state", "evidence", "value", "current"), CASES)
def test_decide_over_every_state_evidence_value_and_attempt_flag(state, evidence, value, current):
    t = r.decide(state, evidence=E(evidence), value=value, is_current_attempt=current)
    key = (state, evidence, value, current)
    if key in APPLIED:
        assert (t.disposition, t.new_state) == ("applied", APPLIED[key])
        new = t.new_state
        assert state not in r.TERMINAL and state != "DELIVERED", (
            "nothing leaves DELIVERED or a terminal"
        )
        if state in r.RANK and new in r.RANK and r.RANK[new] < r.RANK[state]:
            assert (state, evidence, new) in r.NAMED_DESCENTS
        if state == "ACCEPTED" and new in r.TERMINAL:
            assert (state, evidence, new) in r.NAMED_DESCENTS
    else:
        expected = "recorded" if (evidence == "PROVIDER" or key in RECORDED) else "refused"
        assert (t.disposition, t.new_state) == (expected, None)


def test_named_descents_are_exactly_two_and_require_their_preconditions():
    assert r.NAMED_DESCENTS == {
        ("FAILED_TRANSIENT", "RETRY", "PENDING"),
        ("ACCEPTED", "PROVIDER", "FAILED_PERMANENT"),
    }
    at_cap = r.decide(
        "FAILED_TRANSIENT",
        evidence=E.RETRY,
        value=None,
        is_current_attempt=True,
        attempt_count=5,
        cap=5,
    )
    assert at_cap.disposition == "refused"
    earlier = r.decide(
        "ACCEPTED", evidence=E.PROVIDER, value="FAILED_PERMANENT", is_current_attempt=False
    )
    assert earlier.disposition == "recorded" and earlier.new_state is None


def _expected_summary(states, any_attempt):
    """§6.3's table, restated row by row."""
    if any(s in ("PENDING", "IN_FLIGHT") for s in states):
        return "IN_PROGRESS"
    if "OUTCOME_UNKNOWN" in states:
        return "INDETERMINATE"
    successes = [s for s in states if s in ("ACCEPTED", "DELIVERED")]
    if len(successes) == len(states):
        return "SENT"
    if successes:
        return "PARTIAL"
    if "CANCELLED" in states and not any_attempt:
        return "CANCELLED"
    return "FAILED"


def test_summarize_table():
    for n in (1, 2, 3):
        for combo in itertools.combinations_with_replacement(STATES, n):
            for any_attempt in (True, False):
                assert r.summarize(list(combo), any_attempt) == _expected_summary(
                    combo, any_attempt
                ), combo
    assert r.summarize(["ACCEPTED"] * 372 + ["SKIPPED_PLATFORM_POLICY"] * 12, True) == "PARTIAL"


def test_summarize_refuses_empty():
    with pytest.raises(ValueError):
        r.summarize([], False)


def test_unknown_is_never_failed():
    for combo in itertools.combinations_with_replacement(STATES, 3):
        if "OUTCOME_UNKNOWN" in combo:
            assert r.summarize(list(combo), True) != "FAILED"


# --- database-backed ---------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def w(conn):
    return fx.world(conn)


def _job(conn, job_id):
    return conn.execute(
        "SELECT state, attempt_count FROM delivery_jobs WHERE id = ?", (job_id,)
    ).fetchone()


def _campaign(conn, cid):
    return conn.execute("SELECT lifecycle, summary FROM campaigns WHERE id = ?", (cid,)).fetchone()


def _types(conn):
    return [
        row[0] for row in conn.execute("SELECT event_type FROM campaign_events ORDER BY event_seq")
    ]


def _claim(conn, job_id):
    with write_tx(conn):
        return r.reduce(conn, job_id, None, E.CLAIM, None, now=NOW)


def _apply(conn, job_id, attempt_id, evidence, value):
    with write_tx(conn):
        return r.reduce(conn, job_id, attempt_id, evidence, value, now=NOW)


def test_reduce_outside_a_transaction_is_refused(conn, w):
    with pytest.raises(RuntimeError):
        r.reduce(conn, w["job"], None, E.CLAIM, None, now=NOW)


def test_claim_inserts_the_attempt_atomically(conn, w):
    t = _claim(conn, w["job"])
    assert t.disposition == "applied" and t.attempt_id is not None
    assert _job(conn, w["job"]) == ("IN_FLIGHT", 1)
    row = conn.execute(
        "SELECT attempt_no, finished_at, outcome FROM delivery_attempts WHERE id = ?",
        (t.attempt_id,),
    ).fetchone()
    assert row == (1, None, None)
    assert _claim(conn, w["job"]).disposition == "refused"
    assert conn.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 1


def test_claim_refused_unless_sending_and_current_generation(conn, w):
    conn.execute("UPDATE campaigns SET lifecycle = 'SCHEDULED' WHERE id = ?", (w["campaign"],))
    assert _claim(conn, w["job"]).disposition == "refused"
    conn.execute("UPDATE campaigns SET lifecycle = 'SENDING' WHERE id = ?", (w["campaign"],))
    fx.generation(conn, w["campaign"])  # a newer generation becomes current
    assert _claim(conn, w["job"]).disposition == "refused"
    assert _job(conn, w["job"]) == ("PENDING", 0)
    assert conn.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 0


def test_result_completes_the_campaign_in_the_same_transaction(conn, w):
    t = _claim(conn, w["job"])
    assert _apply(conn, w["job"], t.attempt_id, E.RESULT, "ACCEPTED").disposition == "applied"
    assert _campaign(conn, w["campaign"]) == ("COMPLETE", "SENT")
    assert conn.execute("SELECT outcome FROM delivery_attempts").fetchone()[0] == "ACCEPTED"
    assert _types(conn)[-2:] == ["campaign.transport_completed", "campaign.completed"]
    completed = json.loads(
        conn.execute(
            "SELECT payload FROM campaign_events WHERE event_type = 'campaign.completed'"
        ).fetchone()[0]
    )
    assert completed["summary"] == "SENT" and completed["success_count"] == 1


def test_late_webhook_delivered_then_sync_accepted_keeps_delivered(conn, w):
    t = _claim(conn, w["job"])
    assert _apply(conn, w["job"], t.attempt_id, E.PROVIDER, "DELIVERED").disposition == "applied"
    assert _apply(conn, w["job"], t.attempt_id, E.RESULT, "ACCEPTED").disposition == "recorded"
    assert _job(conn, w["job"])[0] == "DELIVERED"
    assert conn.execute("SELECT outcome FROM delivery_attempts").fetchone()[0] == "DELIVERED"


def test_late_failure_from_an_earlier_attempt_does_not_touch_the_job(conn, w):
    first = _claim(conn, w["job"])
    _apply(conn, w["job"], first.attempt_id, E.RESULT, "FAILED_TRANSIENT")
    assert _apply(conn, w["job"], None, E.RETRY, None).disposition == "applied"
    conn.execute("UPDATE campaigns SET lifecycle = 'SENDING' WHERE id = ?", (w["campaign"],))
    second = _claim(conn, w["job"])
    _apply(conn, w["job"], second.attempt_id, E.RESULT, "ACCEPTED")
    late = _apply(conn, w["job"], first.attempt_id, E.PROVIDER, "FAILED_PERMANENT")
    assert late.disposition == "recorded"
    assert _job(conn, w["job"]) == ("ACCEPTED", 2)
    outcomes = [
        row[0] for row in conn.execute("SELECT outcome FROM delivery_attempts ORDER BY attempt_no")
    ]
    assert outcomes == ["FAILED_PERMANENT", "ACCEPTED"]
    assert "delivery.provider_update_refused" in _types(conn)


def test_retry_at_the_cap_is_refused(conn, w):
    t = _claim(conn, w["job"])
    _apply(conn, w["job"], t.attempt_id, E.RESULT, "FAILED_TRANSIENT")
    with write_tx(conn):
        assert (
            r.reduce(conn, w["job"], None, E.RETRY, None, now=NOW, cap=1).disposition == "refused"
        )


def test_recover_and_resolve(conn, w):
    t = _claim(conn, w["job"])
    assert _apply(conn, w["job"], t.attempt_id, E.RECOVER, None).new_state == "OUTCOME_UNKNOWN"
    row = conn.execute("SELECT outcome, finished_at FROM delivery_attempts").fetchone()
    assert row == ("OUTCOME_UNKNOWN", fx.T0)
    assert _campaign(conn, w["campaign"]) == ("COMPLETE", "INDETERMINATE")
    assert _apply(conn, w["job"], None, E.RESOLVE_SENT, None).new_state == "ACCEPTED"
    assert _campaign(conn, w["campaign"]) == ("COMPLETE", "SENT")
    assert _types(conn)[-1] == "campaign.summary_changed"


def _pending_event(conn, ref, status, event_ref):
    conn.execute(
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref,"
        " reported_status, disposition, received_at) VALUES ('whatsapp', ?, ?, ?, 'pending_match', ?)",
        (event_ref, ref, status, fx.T0),
    )


def test_bind_provider_ref_reconciles_pending_events_in_order(conn, w):
    _pending_event(conn, "wa-msg-1", "DELIVERED", "e1")
    _pending_event(conn, "wa-msg-1", "ACCEPTED", "e2")
    _pending_event(conn, "wa-msg-other", "DELIVERED", "e3")
    t = _claim(conn, w["job"])
    with write_tx(conn):
        r.reduce(conn, w["job"], t.attempt_id, E.RESULT, "ACCEPTED", now=NOW)
        assert r.bind_provider_ref(conn, t.attempt_id, "wa-msg-1", now=NOW) == 2
    assert _job(conn, w["job"])[0] == "DELIVERED"
    rows = conn.execute(
        "SELECT provider_event_ref, disposition, attempt_id FROM provider_events ORDER BY id"
    ).fetchall()
    assert rows == [
        ("e1", "applied", t.attempt_id),
        ("e2", "recorded", t.attempt_id),
        ("e3", "pending_match", None),
    ]
    assert _campaign(conn, w["campaign"]) == ("COMPLETE", "SENT")


def test_bind_provider_ref_refuses_a_second_binding(conn, w):
    t = _claim(conn, w["job"])
    with write_tx(conn):
        r.bind_provider_ref(conn, t.attempt_id, "m1", now=NOW)
    with write_tx(conn), pytest.raises(ValueError, match="already bound"):
        r.bind_provider_ref(conn, t.attempt_id, "m2", now=NOW)


def test_summary_is_recomputed_in_the_same_transaction(conn, w):
    t = _claim(conn, w["job"])
    fakes.plant_failure(conn, "campaigns", "UPDATE OF summary")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError, match="planted"):
        _apply(conn, w["job"], t.attempt_id, E.RESULT, "ACCEPTED")
    assert _job(conn, w["job"])[0] == "IN_FLIGHT"
    assert conn.execute("SELECT outcome FROM delivery_attempts").fetchone()[0] is None


def test_transport_completed_is_emitted_once_per_transport(conn, w):
    second_identity = fx.identity(conn, "whatsapp", "+61400000002")
    rid, _ = fx.recipient(conn)
    _, cp = fx.contact_point(conn, rid, second_identity, "whatsapp")
    j2, _ = fx.job(conn, w["generation"], second_identity, "whatsapp")
    fx.origin(conn, j2, cp)
    a1 = _claim(conn, w["job"])
    _apply(conn, w["job"], a1.attempt_id, E.RESULT, "ACCEPTED")
    assert "campaign.transport_completed" not in _types(conn)
    a2 = _claim(conn, j2)
    _apply(conn, j2, a2.attempt_id, E.RESULT, "FAILED_PERMANENT")
    assert _types(conn).count("campaign.transport_completed") == 1
    assert _campaign(conn, w["campaign"]) == ("COMPLETE", "PARTIAL")


def test_cancel_of_every_pending_job_with_no_attempt_is_cancelled(conn, w):
    assert _apply(conn, w["job"], None, E.CANCEL, None).new_state == "CANCELLED"
    assert _campaign(conn, w["campaign"]) == ("COMPLETE", "CANCELLED")
