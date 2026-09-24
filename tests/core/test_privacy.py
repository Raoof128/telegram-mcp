"""comms 5b-4 Task 13: privacy canaries (design §2, §9; R12, R22, S6, G27).

A phone number, a raw Telegram ID and a message body are planted, a campaign is sent
through a crash and recovery, and every byte the database directory holds and every
observable output is searched for them.
"""

import logging

import pytest

from comms.core.campaigns import directory as d
from comms.core.campaigns import drafts
from comms.core.campaigns.resolve import resolve_targets
from comms.core.delivery import freeze
from comms.core.delivery import operations as ops
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.delivery.recovery import recover
from comms.core.storage.db import open_comms_db
from comms.core.storage.migrations import migrate
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

PHONE = "+61400000001"
TG_ID = "987654321"
BODY = "CANARY-BODY-سلام"
CANARIES = [
    PHONE.encode(),
    PHONE[1:].encode(),
    TG_ID.encode(),
    BODY.encode("utf-8"),
    "سلام".encode(),
]


def _scenario(conn, tmp_path, observed):
    """Register, send, crash mid-way, recover, finish; append every return value to ``observed``."""
    tx = {"telegram": fakes.FakeTelegram(conn=conn), "whatsapp": fakes.FakeWhatsApp(conn=conn)}
    lease = ExecutorLease(fakes.FakeLock())
    rcp = d.add_recipient(conn, now=NOW)
    observed += [
        rcp,
        d.add_contact_point(conn, rcp, "whatsapp", PHONE, normalize=fx.wa, now=NOW),
        d.add_contact_point(conn, rcp, "telegram", f"user:{TG_ID}", normalize=fx.tg, now=NOW),
    ]
    cmp = drafts.create_campaign(conn, "Nowruz", now=NOW)
    drafts.set_content(conn, cmp, canonical=BODY, now=NOW)
    drafts.set_targets(
        conn, cmp, {"recipients": [rcp]}, frozenset({"telegram", "whatsapp"}), now=NOW
    )
    drafts.validate(conn, cmp, now=NOW)
    observed += [
        cmp,
        resolve_targets(conn, {"recipients": [rcp]}, frozenset({"telegram", "whatsapp"})),
    ]
    observed.append(freeze.send(conn, cmp, tx, now=NOW))
    tx["whatsapp"].script[PHONE] = ["crash_after_accept"]
    with pytest.raises(fakes.SimulatedCrash):
        Engine(conn, tx, clock=lambda: NOW).execute(lease, cmp)
    observed.append(recover(lease, conn, now=NOW))
    observed.append(Engine(conn, tx, clock=lambda: NOW).execute(lease, cmp))
    job = conn.execute("SELECT ref FROM delivery_jobs WHERE state = 'OUTCOME_UNKNOWN'").fetchone()[
        0
    ]
    observed.append(ops.resolve_outcome(conn, job, "sent", now=NOW))
    observed.append(
        ops.record_provider_update(
            conn, "telegram", "evt-1", "telegram-msg-1", "DELIVERED", now=NOW
        )
    )
    observed.append(ops.retry_failed(conn, cmp, now=NOW))
    return cmp, tx


def _files(directory):
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_canaries_absent_from_every_file_in_the_database_directory(tmp_path, journal_mode):
    directory = tmp_path / "db"
    directory.mkdir()
    conn = open_comms_db(directory / "comms.db", fx.KEY)
    assert conn.execute(f"PRAGMA journal_mode = {journal_mode}").fetchone()[0] == journal_mode
    migrate(conn)
    _scenario(conn, tmp_path, [])
    seen = _files(directory)
    if journal_mode == "wal":
        assert "comms.db-wal" in seen, "the WAL must exist while open, or this proves nothing"
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)" if journal_mode == "wal" else "SELECT 1")
    for snapshot in (seen, _files(directory)):
        for name, data in snapshot.items():
            for canary in CANARIES:
                assert canary not in data, (name, canary)
    conn.close()
    for name, data in _files(directory).items():
        for canary in CANARIES:
            assert canary not in data, (name, canary)


def _text(value) -> str:
    return f"{value!r} {value!s}"


def test_canaries_absent_from_events_returns_reprs_exceptions_and_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    conn = fx.migrated(tmp_path)
    observed: list = []
    cmp, tx = _scenario(conn, tmp_path, observed)
    errors = []
    rcp = conn.execute("SELECT ref FROM recipients").fetchone()[0]
    for call in (
        lambda: d.add_contact_point(
            conn, d.add_recipient(conn, now=NOW), "whatsapp", PHONE, normalize=fx.wa, now=NOW
        ),
        lambda: d.add_contact_point(
            conn, rcp, "whatsapp", "+61400000009", normalize=fx.wa, now=NOW
        ),
        lambda: d.add_contact_point(conn, rcp, "whatsapp", f"bad{PHONE}", normalize=fx.wa, now=NOW),
        lambda: drafts.set_content(conn, cmp, canonical=BODY, now=NOW),
        lambda: freeze.send(conn, cmp, tx, now=NOW),
        lambda: ops.resolve_outcome(conn, "djb_" + "a" * 26, "sent", now=NOW),
        lambda: ops.record_provider_update(conn, "whatsapp", PHONE, PHONE, "READ", now=NOW),
    ):
        with pytest.raises(Exception) as caught:
            call()
        errors.append(caught.value)
    events = [
        row for row in conn.execute("SELECT event_type, campaign_ref, payload FROM campaign_events")
    ]
    assert len(events) > 10
    haystacks = (
        [_text(v) for v in observed] + [_text(e) for e in errors] + [_text(r) for r in events]
    )
    haystacks += [record.getMessage() for record in caplog.records]
    assert any(cmp in text for text in haystacks), "the sweep must see the campaign's own outputs"
    assert _leaks(haystacks) == []


def _leaks(haystacks: list[str]) -> list[tuple[str, str]]:
    return [
        (canary, text[:120])
        for text in haystacks
        for canary in (PHONE, PHONE[1:], TG_ID, BODY, "سلام")
        if canary in text
    ]


def test_the_canary_sweep_has_teeth(tmp_path):
    conn = fx.migrated(tmp_path)
    conn.execute(
        "INSERT INTO campaign_events (event_ref, event_type, ts, payload) VALUES (?, ?, ?, ?)",
        ("cev_" + "a" * 26, "campaign.modified", fx.T0, f'{{"note": "{PHONE}"}}'),
    )
    rows = [_text(r) for r in conn.execute("SELECT * FROM campaign_events")]
    assert _leaks(rows) and _leaks([repr(ValueError(BODY))])
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"xx" + PHONE.encode() + b"yy")
    assert any(c in plain.read_bytes() for c in CANARIES)


def test_transport_credentials_absent_from_campaign_data(tmp_path):
    conn = fx.migrated(tmp_path)
    _scenario(conn, tmp_path, [])
    credential = fakes.FakeWhatsApp.credential
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    assert "delivery_jobs" in tables
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table}"):
            assert credential not in repr(row), table
    assert credential.encode() not in (tmp_path / "comms.db").read_bytes()
