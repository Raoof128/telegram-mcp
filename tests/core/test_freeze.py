"""comms 5b-4 Task 9: freeze — send, schedule, unschedule, pre-send cancel
(design §5.1, §5.4, §6.2, §7.1, §8, §9; R3, R6, R8, R14, R18, S5, S8, G1, G7, G9, G16, F1)."""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
import sqlcipher3

from comms.core import refs
from comms.core.campaigns import directory as d
from comms.core.campaigns import drafts
from comms.core.canonical import jcs_dumps
from comms.core.delivery import freeze as f
from tests.core import fakes
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)
FRIDAY = datetime(2026, 9, 25, 8, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def transports(conn):
    return {"telegram": fakes.FakeTelegram(conn=conn), "whatsapp": fakes.FakeWhatsApp(conn=conn)}


def ready(conn, targets, transports=frozenset({"whatsapp"}), body="Happy Nowruz"):
    cmp = drafts.create_campaign(conn, "T", now=NOW)
    drafts.set_content(conn, cmp, canonical=body, now=NOW)
    drafts.set_targets(conn, cmp, targets, transports, now=NOW)
    drafts.validate(conn, cmp, now=NOW)
    return cmp


def person(conn, phone=None, tg=None):
    rcp = d.add_recipient(conn, now=NOW)
    pts = {}
    if phone:
        pts["wa"] = d.add_contact_point(conn, rcp, "whatsapp", phone, normalize=fx.wa, now=NOW)
    if tg:
        pts["tg"] = d.add_contact_point(conn, rcp, "telegram", tg, normalize=fx.tg, now=NOW)
    return rcp, pts


def jobs(conn, gen=None):
    sql = (
        "SELECT j.ref, j.transport, i.identity, j.state, j.idempotency_key, j.payload_digest, j.skip_reason"
        " FROM delivery_jobs j JOIN delivery_identities i ON i.id = j.identity_id"
        " JOIN generations g ON g.id = j.generation_id"
    )
    rows = conn.execute(
        sql + (" WHERE g.ref = ?" if gen else "") + " ORDER BY j.id", (gen,) if gen else ()
    ).fetchall()
    return rows


def campaign_row(conn, cmp):
    return conn.execute(
        "SELECT lifecycle, summary, current_generation_id FROM campaigns WHERE ref = ?", (cmp,)
    ).fetchone()


def events(conn, kind):
    return [
        json.loads(p)
        for (p,) in conn.execute(
            "SELECT payload FROM campaign_events WHERE event_type = ? ORDER BY event_seq", (kind,)
        )
    ]


def test_one_send_creates_one_immutable_generation(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    gen = f.send(conn, cmp, transports, now=NOW)
    assert gen.startswith("gen_")
    lifecycle, summary, _ = campaign_row(conn, cmp)
    assert (lifecycle, summary) == ("SENDING", "IN_PROGRESS")
    assert conn.execute("SELECT ref, status, send_at FROM generations").fetchall() == [
        (gen, "active", "2026-09-24T00:00:00.000000Z")
    ]
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
        conn.execute("UPDATE generations SET content = '{}'")
    started = events(conn, "campaign.send_started")[0]
    assert (
        started["generation"] == gen and started["job_count"] == 1 and started["pending_count"] == 1
    )
    assert "snapshot_digest" not in started


def test_same_identity_via_destination_and_contact_point_gets_one_job(conn, transports):
    loc = d.add_location(conn, "L", now=NOW)
    dm = d.add_destination(conn, loc, "telegram", "private:12345", "DM", normalize=fx.tg, now=NOW)
    rcp, pts = person(conn, tg="user:12345")
    cmp = ready(conn, {"destinations": [dm], "recipients": [rcp]}, frozenset({"telegram"}))
    f.send(conn, cmp, transports, now=NOW)
    [job] = jobs(conn)
    origins = {r[0] for r in conn.execute("SELECT endpoint_ref FROM job_origins")}
    assert job[2] == "12345" and origins == {dm, pts["tg"]}


def test_duplicate_whatsapp_recipient_gets_one_job(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    auds = [d.add_audience(conn, str(i), now=NOW) for i in range(3)]
    for aud in auds:
        d.add_audience_member(conn, aud, rcp)
    f.send(conn, ready(conn, {"audiences": auds}), transports, now=NOW)
    assert len(jobs(conn)) == 1
    assert conn.execute("SELECT count(*) FROM job_origins").fetchone()[0] == 3


def test_same_person_gets_one_job_per_selected_transport(conn, transports):
    rcp, _ = person(conn, phone="+61400000001", tg="user:5")
    f.send(
        conn,
        ready(conn, {"recipients": [rcp]}, frozenset({"telegram", "whatsapp"})),
        transports,
        now=NOW,
    )
    assert sorted(j[1] for j in jobs(conn)) == ["telegram", "whatsapp"]


def test_zero_eligible_endpoints_refuses_and_leaves_ready(conn, transports):
    rcp, pts = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    d.opt_out(conn, pts["wa"], now=NOW)
    with pytest.raises(f.NoEligibleEndpoints, match="^NO_ELIGIBLE_ENDPOINTS$"):
        f.send(conn, cmp, transports, now=NOW)
    rcp2, _ = person(conn, phone="+61400000002")
    cmp2 = ready(conn, {"recipients": [rcp2]})
    transports["whatsapp"].script["+61400000002"] = ["ineligible"]
    with pytest.raises(f.NoEligibleEndpoints):
        f.send(conn, cmp2, transports, now=NOW)
    for c in (cmp, cmp2):
        assert campaign_row(conn, c) == ("READY", None, None)
    assert conn.execute("SELECT count(*) FROM generations").fetchone()[0] == 0


def test_platform_ineligible_becomes_a_skipped_job_not_a_failure(conn, transports):
    r1, _ = person(conn, phone="+61400000001")
    r2, _ = person(conn, phone="+61400000002")
    transports["whatsapp"].script["+61400000002"] = ["ineligible"]
    f.send(conn, ready(conn, {"recipients": [r1, r2]}), transports, now=NOW)
    states = sorted((j[3], j[6]) for j in jobs(conn))
    assert states == [("PENDING", None), ("SKIPPED_PLATFORM_POLICY", "platform_ineligible")]


def test_no_deliver_happens_during_freeze(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    f.send(conn, ready(conn, {"recipients": [rcp]}), transports, now=NOW)
    calls = transports["whatsapp"].calls
    assert [c[0] for c in calls] == ["prepare"] and all(c[2] for c in calls)
    assert transports["whatsapp"].sent == []


BASE = {
    "cmp": "cmp_" + "a" * 26,
    "gen": "gen_" + "b" * 26,
    "send_at": "2026-09-25T08:00:00.000000Z",
    "content": {"canonical": "hi"},
    "transports": ["whatsapp"],
    "jobs": [
        {
            "transport": "whatsapp",
            "delivery_identity": "+61400000001",
            "payload_digest": "c" * 64,
            "initial_state": "PENDING",
            "skip_reason": None,
        }
    ],
}


def test_snapshot_digest_is_stable():
    here = f.snapshot_digest(**BASE)
    assert here == f.snapshot_digest(**json.loads(json.dumps(BASE)))
    code = (
        "import json,sys; from comms.core.delivery.freeze import snapshot_digest;"
        "print(snapshot_digest(**json.loads(sys.argv[1])))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, json.dumps(BASE)], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == here


def test_snapshot_digest_moves_with_every_field():
    base = f.snapshot_digest(**BASE)
    changes = [
        {"cmp": "cmp_" + "z" * 26},
        {"gen": "gen_" + "z" * 26},
        {"send_at": "2026-09-25T08:00:00.000001Z"},
        {"content": {"canonical": "hi!"}},
        {"transports": ["telegram", "whatsapp"]},
    ]
    for field in (
        "transport",
        "delivery_identity",
        "payload_digest",
        "initial_state",
        "skip_reason",
    ):
        job = dict(BASE["jobs"][0])
        job[field] = {
            "transport": "telegram",
            "delivery_identity": "+61400000002",
            "payload_digest": "d" * 64,
            "initial_state": "SKIPPED_PLATFORM_POLICY",
            "skip_reason": "platform_ineligible",
        }[field]
        changes.append({"jobs": [job]})
    for change in changes:
        assert f.snapshot_digest(**{**BASE, **change}) != base, change


def _stored_snapshot(conn, gen):
    cmp, send_at, content = conn.execute(
        "SELECT c.ref, g.send_at, g.content FROM generations g JOIN campaigns c ON c.id = g.campaign_id"
        " WHERE g.ref = ?",
        (gen,),
    ).fetchone()
    rows = jobs(conn, gen)
    return f.snapshot_digest(
        cmp=cmp,
        gen=gen,
        send_at=send_at,
        content=json.loads(content),
        transports=json.loads(
            conn.execute("SELECT options FROM campaigns WHERE ref = ?", (cmp,)).fetchone()[0]
        )["transports"],
        jobs=[
            {
                "transport": r[1],
                "delivery_identity": r[2],
                "payload_digest": r[5],
                "initial_state": "PENDING" if r[5] else "SKIPPED_PLATFORM_POLICY",
                "skip_reason": r[6],
            }
            for r in rows
        ],
    )


def test_the_freeze_commits_the_digest_of_what_it_wrote(conn, transports):
    r1, _ = person(conn, phone="+61400000001")
    r2, _ = person(conn, phone="+61400000002", tg="user:9")
    transports["whatsapp"].script["+61400000002"] = ["ineligible"]
    gen = f.send(
        conn,
        ready(conn, {"recipients": [r1, r2]}, frozenset({"telegram", "whatsapp"})),
        transports,
        now=NOW,
    )
    stored = conn.execute(
        "SELECT snapshot_digest FROM generations WHERE ref = ?", (gen,)
    ).fetchone()[0]
    assert stored == _stored_snapshot(conn, gen)


def test_three_digests_are_distinct_and_domain_separated(conn, transports):
    rcp, pts = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    gen = f.send(conn, cmp, transports, now=NOW)
    started = events(conn, "campaign.send_started")[0]
    [job] = jobs(conn, gen)
    target = f.target_digest({"recipients": [rcp]}, frozenset({"whatsapp"}))
    recipients = f.recipient_digest([(job[0], "whatsapp", (pts["wa"],), "PENDING")])
    snapshot = conn.execute("SELECT snapshot_digest FROM generations").fetchone()[0]
    assert (started["target_digest"], started["recipient_digest"]) == (target, recipients)
    assert len({target, recipients, snapshot}) == 3
    preimage = jcs_dumps(
        {
            "targets": {"audiences": [], "destinations": [], "locations": [], "recipients": [rcp]},
            "transports": ["whatsapp"],
        }
    )
    assert target == hashlib.sha256(b"comms-campaign-target/v1\x00" + preimage).hexdigest()


def test_digest_builders_accept_only_refs():
    with pytest.raises(ValueError):
        f.target_digest({"recipients": ["+61400000001"]}, frozenset({"whatsapp"}))
    with pytest.raises(ValueError):
        f.recipient_digest([("djb_" + "a" * 26, "whatsapp", ("+61400000001",), "PENDING")])
    with pytest.raises(ValueError):
        f.recipient_digest([("987654321", "telegram", ("rct_" + "a" * 26,), "PENDING")])


def test_idempotency_key_binds_generation():
    a = f.idempotency_key("cmp_" + "a" * 26, "gen_" + "a" * 26, "whatsapp", "+61400000001")
    b = f.idempotency_key("cmp_" + "a" * 26, "gen_" + "b" * 26, "whatsapp", "+61400000001")
    preimage = jcs_dumps(
        {
            "campaign": "cmp_" + "a" * 26,
            "delivery_identity": "+61400000001",
            "generation": "gen_" + "a" * 26,
            "transport": "whatsapp",
        }
    )
    assert a != b and a == hashlib.sha256(b"comms-delivery-idem/v1\x00" + preimage).hexdigest()


def test_edit_and_reschedule_mints_new_keys(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    first = f.schedule(conn, cmp, FRIDAY, transports, now=NOW)
    f.unschedule(conn, cmp, now=NOW)
    drafts.edit(conn, cmp, now=NOW)
    drafts.set_content(conn, cmp, canonical="Edited", now=NOW)
    drafts.validate(conn, cmp, now=NOW)
    second = f.schedule(conn, cmp, FRIDAY, transports, now=NOW)
    old, new = jobs(conn, first), jobs(conn, second)
    assert {j[4] for j in old}.isdisjoint({j[4] for j in new})
    assert [j[3] for j in old] == ["CANCELLED"] and [j[3] for j in new] == ["PENDING"]
    assert conn.execute("SELECT ref, status FROM generations ORDER BY id").fetchall() == [
        (first, "discarded"),
        (second, "active"),
    ]


def test_unschedule_with_a_skipped_job_cancels_only_pending(conn, transports):
    r1, _ = person(conn, phone="+61400000001")
    r2, _ = person(conn, phone="+61400000002")
    transports["whatsapp"].script["+61400000002"] = ["ineligible"]
    cmp = ready(conn, {"recipients": [r1, r2]})
    gen = f.schedule(conn, cmp, FRIDAY, transports, now=NOW)
    f.unschedule(conn, cmp, now=NOW)
    assert sorted(j[3] for j in jobs(conn, gen)) == ["CANCELLED", "SKIPPED_PLATFORM_POLICY"]
    assert events(conn, "campaign.unscheduled")[0]["cancelled_count"] == 1
    assert events(conn, "campaign.transport_completed") == []  # nothing was ever sent


def test_unschedule_and_scheduled_cancel_clear_the_current_generation(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    a, b = ready(conn, {"recipients": [rcp]}), ready(conn, {"recipients": [rcp]})
    for cmp in (a, b):
        f.schedule(conn, cmp, FRIDAY, transports, now=NOW)
    f.unschedule(conn, a, now=NOW)
    report = f.cancel(conn, b, now=NOW)
    assert campaign_row(conn, a) == ("READY", None, None)
    assert campaign_row(conn, b) == ("CANCELLED", None, None)
    assert report == f.CancelReport(cancelled_before_send=1, already_sent=0, currently_in_flight=0)
    assert (
        conn.execute("SELECT count(*) FROM generations WHERE status = 'active'").fetchone()[0] == 0
    )


def test_scheduled_content_is_frozen(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    f.schedule(conn, cmp, FRIDAY, transports, now=NOW)
    with pytest.raises(drafts.LifecycleError):
        drafts.set_content(conn, cmp, canonical="changed", now=NOW)
    with pytest.raises(drafts.LifecycleError):
        drafts.edit(conn, cmp, now=NOW)
    assert events(conn, "campaign.scheduled")[0]["send_at"] == "2026-09-25T08:00:00.000000Z"


def test_cancel_before_send_from_each_pre_send_state(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    draft = drafts.create_campaign(conn, "D", now=NOW)
    ready_one = ready(conn, {"recipients": [rcp]})
    for cmp in (draft, ready_one):
        assert f.cancel(conn, cmp, now=NOW) == f.CancelReport(0, 0, 0)
        assert campaign_row(conn, cmp)[0] == "CANCELLED"
    with pytest.raises(drafts.LifecycleError, match="^campaign cannot be cancelled$"):
        f.cancel(conn, draft, now=NOW)
    assert len(events(conn, "campaign.cancelled")) == 2


def test_send_and_schedule_require_ready_and_their_transports(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    with pytest.raises(drafts.LifecycleError, match="^transport unavailable$"):
        f.send(conn, cmp, {"telegram": transports["telegram"]}, now=NOW)
    f.send(conn, cmp, transports, now=NOW)
    with pytest.raises(drafts.LifecycleError, match="^campaign is not ready$"):
        f.send(conn, cmp, transports, now=NOW)
    with pytest.raises(ValueError):
        naive = datetime(2026, 9, 25)  # noqa: DTZ001 -- the naive input under test
        f.schedule(conn, ready(conn, {"recipients": [rcp]}), naive, transports, now=NOW)


def test_offset_schedule_time_is_stored_as_utc(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    sydney = datetime(2026, 9, 25, 18, tzinfo=__import__("datetime").timezone(timedelta(hours=10)))
    f.schedule(conn, ready(conn, {"recipients": [rcp]}), sydney, transports, now=NOW)
    assert (
        conn.execute("SELECT send_at FROM generations").fetchone()[0]
        == "2026-09-25T08:00:00.000000Z"
    )


def test_freeze_is_one_transaction(conn, transports):
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    fakes.plant_failure(conn, "campaign_events", "INSERT")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError, match="planted"):
        f.send(conn, cmp, transports, now=NOW)
    for table in ("generations", "delivery_jobs", "job_origins"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert campaign_row(conn, cmp) == ("READY", None, None)


def test_a_payload_whose_digest_lies_fails_closed(conn, transports):
    from comms.core.delivery.transport import PreparedPayload

    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    liar = fakes.FakeWhatsApp(conn=conn)
    liar.prepare = lambda intent, send_at: PreparedPayload(data=b"x", digest="0" * 64)
    with pytest.raises(ValueError, match="payload digest"):
        f.send(conn, cmp, {**transports, "whatsapp": liar}, now=NOW)
    assert campaign_row(conn, cmp)[0] == "READY"
    assert refs.kind_of(cmp) == "campaign"
