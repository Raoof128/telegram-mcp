"""comms 5b-4 Task 7: drafts (DRAFT ⇄ READY) and the typed, atomic event log (design §6.1, §9, S6)."""

import json
from datetime import UTC, datetime

import pytest
import sqlcipher3

from comms.core import refs
from comms.core.campaigns import directory as d
from comms.core.campaigns import drafts
from comms.core.campaigns.events import EVENT_TYPES, ReasonCode, append_event
from comms.core.storage.db import write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)
SPEC_EVENTS = {
    "campaign.created",
    "campaign.modified",
    "campaign.validated",
    "campaign.scheduled",
    "campaign.unscheduled",
    "campaign.send_started",
    "campaign.transport_completed",
    "campaign.retry_started",
    "campaign.completed",
    "campaign.summary_changed",
    "campaign.cancelled",
    "delivery.outcome_resolved",
    "delivery.provider_update_refused",
}


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _events(conn):
    return [
        (t, json.loads(p))
        for t, p in conn.execute(
            "SELECT event_type, payload FROM campaign_events ORDER BY event_seq"
        )
    ]


def _lifecycle(conn, cmp):
    return conn.execute("SELECT lifecycle FROM campaigns WHERE ref = ?", (cmp,)).fetchone()[0]


def _ready(conn):
    cmp = drafts.create_campaign(conn, "Nowruz", now=NOW)
    rcp = d.add_recipient(conn, now=NOW)
    drafts.set_content(conn, cmp, canonical="Happy Nowruz", fa="نوروز مبارک", now=NOW)
    drafts.set_targets(conn, cmp, {"recipients": [rcp]}, frozenset({"whatsapp"}), now=NOW)
    drafts.validate(conn, cmp, now=NOW)
    return cmp


def test_event_types_are_exactly_the_spec_list():
    assert EVENT_TYPES == SPEC_EVENTS


def test_happy_path_emits_each_event_in_its_transaction(conn):
    cmp = _ready(conn)
    assert _lifecycle(conn, cmp) == "READY"
    drafts.edit(conn, cmp, now=NOW)
    assert _lifecycle(conn, cmp) == "DRAFT"
    assert [t for t, _ in _events(conn)] == [
        "campaign.created",
        "campaign.modified",
        "campaign.modified",
        "campaign.validated",
        "campaign.modified",
    ]
    assert all(p.get("lifecycle") in {"DRAFT", "READY"} for _, p in _events(conn))
    content = json.loads(conn.execute("SELECT content FROM campaigns").fetchone()[0])
    assert content == {"canonical": "Happy Nowruz", "fa": "نوروز مبارک"}


@pytest.mark.parametrize("lifecycle", ["READY", "SCHEDULED", "SENDING", "COMPLETE", "CANCELLED"])
def test_content_edits_are_refused_outside_draft(conn, lifecycle):
    cmp = drafts.create_campaign(conn, "T", now=NOW)
    conn.execute("UPDATE campaigns SET lifecycle = ? WHERE ref = ?", (lifecycle, cmp))
    for call in (
        lambda: drafts.set_content(conn, cmp, canonical="x", now=NOW),
        lambda: drafts.set_targets(conn, cmp, {}, frozenset({"whatsapp"}), now=NOW),
        lambda: drafts.validate(conn, cmp, now=NOW),
    ):
        with pytest.raises(drafts.LifecycleError, match="^campaign is not a draft$"):
            call()
    if lifecycle != "READY":
        with pytest.raises(drafts.LifecycleError, match="^campaign is not ready$"):
            drafts.edit(conn, cmp, now=NOW)


def test_validate_refuses_missing_body_transport_or_targets(conn):
    rcp = d.add_recipient(conn, now=NOW)
    for body, targets, transports in (
        (None, {"recipients": [rcp]}, {"whatsapp"}),
        ("hi", {"recipients": []}, {"whatsapp"}),
        ("hi", {"recipients": [rcp]}, set()),
    ):
        cmp = drafts.create_campaign(conn, "T", now=NOW)
        if body:
            drafts.set_content(conn, cmp, canonical=body, now=NOW)
        drafts.set_targets(conn, cmp, targets, frozenset(transports), now=NOW)
        with pytest.raises(drafts.LifecycleError, match="^campaign is incomplete$"):
            drafts.validate(conn, cmp, now=NOW)
        assert _lifecycle(conn, cmp) == "DRAFT"


def test_unknown_target_fails_closed_at_validate(conn):
    cmp = drafts.create_campaign(conn, "T", now=NOW)
    drafts.set_content(conn, cmp, canonical="hi", now=NOW)
    drafts.set_targets(
        conn, cmp, {"audiences": [refs.mint("audience")]}, frozenset({"whatsapp"}), now=NOW
    )
    with pytest.raises(d.DirectoryError, match="^unknown target$"):
        drafts.validate(conn, cmp, now=NOW)


def test_content_and_target_shapes_are_checked(conn):
    cmp = drafts.create_campaign(conn, "T", now=NOW)
    bad_media = [{"sha256": "x", "mime": "image/png", "name": "a.png", "size": 1}]
    for call in (
        lambda: drafts.set_content(conn, cmp, media=bad_media, now=NOW),
        lambda: drafts.set_content(conn, cmp, links=["ok", 5], now=NOW),
        lambda: drafts.set_content(conn, cmp, canonical=3, now=NOW),
        lambda: drafts.set_targets(conn, cmp, {"people": []}, frozenset({"whatsapp"}), now=NOW),
        lambda: drafts.set_targets(conn, cmp, {}, frozenset({"sms"}), now=NOW),
    ):
        with pytest.raises(ValueError):
            call()
    good = [{"sha256": "a" * 64, "mime": "image/png", "name": "a.png", "size": 10}]
    drafts.set_content(conn, cmp, media=good, links=["https://example.org"], now=NOW)


def test_unknown_campaign_fails_closed(conn):
    with pytest.raises(drafts.LifecycleError, match="^unknown campaign$"):
        drafts.validate(conn, refs.mint("campaign"), now=NOW)
    with pytest.raises(drafts.LifecycleError, match="^unknown campaign$"):
        drafts.edit(conn, "junk", now=NOW)


FREE_TEXT = ["hello", "alice", "+61400000001", "Happy Nowruz", "987654321", "sydney", "OPERATOR "]
STRING_KEYS = [
    "generation",
    "job",
    "attempt",
    "transport",
    "target_digest",
    "recipient_digest",
    "snapshot_digest",
    "summary",
    "lifecycle",
    "reason",
    "send_at",
    "verdict",
    "status",
]


@pytest.mark.parametrize("key", STRING_KEYS)
def test_event_payload_refuses_free_text_under_every_key(conn, key):
    for value in FREE_TEXT:
        with write_tx(conn), pytest.raises(ValueError):
            append_event(conn, "campaign.modified", None, {key: value}, now=NOW)


def test_event_payload_refuses_unknown_keys_bool_counts_and_wrong_ref_kinds(conn):
    bad = [
        {"body": "x"},
        {"job_count": True},
        {"job_count": -1},
        {"job_count": 1.0},
        {"job": refs.mint("campaign")},
        {"generation": refs.mint("job")},
        {"attempt_no": 0},
        {"send_at": "2026-09-24T00:00:00Z"},
        {"reason": "operator"},
        {"transport": "sms"},
    ]
    with write_tx(conn):
        for payload in bad:
            with pytest.raises(ValueError):
                append_event(conn, "campaign.modified", None, payload, now=NOW)
        with pytest.raises(ValueError):
            append_event(conn, "campaign.exploded", None, {}, now=NOW)
        with pytest.raises(ValueError):
            append_event(conn, "campaign.modified", "cmp_bad", {}, now=NOW)
        ok = {
            "job": refs.mint("job"),
            "job_count": 3,
            "reason": ReasonCode.OPERATOR,
            "send_at": "2026-09-24T00:00:00.000000Z",
            "target_digest": "a" * 64,
            "summary": "SENT",
            "lifecycle": "COMPLETE",
            "transport": "whatsapp",
            "verdict": "sent",
            "status": "DELIVERED",
            "attempt_no": 1,
        }
        assert append_event(
            conn, "campaign.modified", refs.mint("campaign"), ok, now=NOW
        ).startswith("cev_")


def test_append_event_requires_a_transaction(conn):
    with pytest.raises(RuntimeError):
        append_event(conn, "campaign.modified", None, {}, now=NOW)


def test_event_is_rolled_back_with_its_transaction(conn):
    with pytest.raises(RuntimeError, match="boom"), write_tx(conn):
        append_event(conn, "campaign.modified", None, {}, now=NOW)
        raise RuntimeError("boom")
    assert _events(conn) == []


def test_a_state_change_is_rolled_back_with_its_event(conn):
    fakes.plant_failure(conn, "campaign_events", "INSERT")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
        drafts.create_campaign(conn, "T", now=NOW)
    assert conn.execute("SELECT count(*) FROM campaigns").fetchone()[0] == 0


def test_event_seq_is_global_and_increasing(conn):
    a = drafts.create_campaign(conn, "A", now=NOW)
    drafts.create_campaign(conn, "B", now=NOW)
    drafts.set_content(conn, a, canonical="x", now=NOW)
    seqs = [r[0] for r in conn.execute("SELECT event_seq FROM campaign_events ORDER BY rowid")]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
