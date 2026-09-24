"""comms v0.3 Task C21: the MTProto update stream consumer (A24, A42)."""

from datetime import UTC, datetime

import pytest
from telethon.tl import types

from comms.core.delivery import freeze
from comms.core.delivery.engine import Engine, EngineCrash, ExecutorLease
from comms.core.delivery.recovery import recover
from comms.transports.telegram.telegram.telethon_adapter import (
    TelegramConfig,
    TelethonSession,
    UpdateStreamTaken,
    neutral_updates,
)
from comms.transports.telegram.telegram.updates_view import NeutralUpdate
from comms.transports.telegram.user.send import random_id_for
from comms.transports.telegram.user.updates import UserUpdateConsumer
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready
from tests.telegram.fake_client import FakeClient

SENT = datetime(2026, 9, 24, 12, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _message_update(mid=10, text="salam"):
    return NeutralUpdate(
        "message", chat="-1000000000077", message_id=mid, payload={"text": text, "sender_id": "42"}
    )


def test_update_message_id_binds_the_attempt_ref(conn):
    transport = fakes.KeyedTelegram(conn, key_of=lambda idem: str(random_id_for(idem)))
    rcp, _ = person(conn, tg="channel:77")
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"telegram"}))
    freeze.send(conn, cmp, {"telegram": transport}, now=NOW)
    lease = ExecutorLease(fakes.FakeLock())
    with pytest.raises(EngineCrash):
        Engine(
            conn, {"telegram": transport}, clock=lambda: NOW, crash_at="after_deliver_before_record"
        ).execute(lease, cmp)
    recover(lease, conn, now=NOW)
    key = conn.execute("SELECT idempotency_key FROM delivery_jobs").fetchone()[0]
    consumer = UserUpdateConsumer(conn, clock=lambda: NOW)
    report = consumer.consume(
        [NeutralUpdate("message_id", message_id=555, random_id=random_id_for(key))]
    )
    assert report.bound == 1
    assert (
        conn.execute("SELECT provider_message_ref FROM delivery_attempts").fetchone()[0]
        == "-1000000000077:555"
    )


def test_duplicate_update_harmless(conn):
    events = []
    consumer = UserUpdateConsumer(
        conn, clock=lambda: NOW, sink=lambda c, event: events.append(event)
    )
    first = consumer.consume(
        [_message_update(), NeutralUpdate("message_id", message_id=1, random_id=99)]
    )
    again = consumer.consume(
        [_message_update(), NeutralUpdate("message_id", message_id=1, random_id=99)]
    )
    assert (first.ingested, first.bound, first.unmatched) == (1, 0, 1)
    assert (again.ingested, again.duplicates) == (0, 1)
    assert len(events) == 1 and events[0].provider_event_ref == "-1000000000077:10"
    assert events[0].transport == "telegram" and events[0].kind == "message"
    assert "salam" not in repr(events[0])
    assert conn.execute("SELECT count(*) FROM user_updates").fetchone()[0] == 1


def test_a_failing_sink_rolls_back_its_update(conn):
    def sink(c, event):
        raise RuntimeError("downstream refused")

    with pytest.raises(RuntimeError):
        UserUpdateConsumer(conn, clock=lambda: NOW, sink=sink).consume([_message_update()])
    assert conn.execute("SELECT count(*) FROM user_updates").fetchone()[0] == 0


async def test_single_session_owner(tmp_path):
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: FakeClient(),
    )
    session.claim_updates("consumer-a")
    with pytest.raises(UpdateStreamTaken):
        session.claim_updates("consumer-b")
    session.claim_updates("consumer-a")  # the owner may re-claim
    session.release_updates("consumer-b")  # not the owner: no effect
    with pytest.raises(UpdateStreamTaken):
        session.claim_updates("consumer-b")
    session.release_updates("consumer-a")
    session.claim_updates("consumer-b")


def test_the_adapter_translates_raw_updates():
    raw = types.Updates(
        updates=[
            types.UpdateMessageID(id=555, random_id=7),
            types.UpdateNewChannelMessage(
                types.Message(
                    id=10,
                    peer_id=types.PeerChannel(77),
                    date=SENT,
                    message="salam",
                    from_id=types.PeerUser(42),
                ),
                pts=1,
                pts_count=1,
            ),
            types.UpdateNewMessage(
                types.Message(id=11, peer_id=types.PeerUser(42), date=SENT, message="hi"),
                pts=2,
                pts_count=1,
            ),
            types.UpdateUserTyping(user_id=42, action=types.SendMessageTypingAction()),
        ],
        users=[],
        chats=[],
        date=None,
        seq=0,
    )
    assert neutral_updates(raw) == [
        NeutralUpdate("message_id", message_id=555, random_id=7),
        NeutralUpdate(
            "message",
            chat="-1000000000077",
            message_id=10,
            payload={"text": "salam", "sender_id": "42", "sent_at": "2026-09-24T12:00:00Z"},
        ),
        NeutralUpdate(
            "message",
            chat="42",
            message_id=11,
            payload={"text": "hi", "sender_id": None, "sent_at": "2026-09-24T12:00:00Z"},
        ),
    ]
    short = types.UpdateShortSentMessage(id=5, pts=1, pts_count=1, date=SENT)
    assert neutral_updates(short) == []  # a send's own result is handled by the send
