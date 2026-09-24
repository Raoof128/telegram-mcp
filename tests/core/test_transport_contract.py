"""comms 5b-4 Task 6: the DeliveryTransport contract (design §4, S3) and the fakes."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
import sqlcipher3

from comms.core.delivery import transport as t
from comms.core.storage.db import TransactionIOError, io_guard, write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)
PHONE = "+61400000001"


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _frozen(identity=PHONE, transport="whatsapp", payload=None):
    payload = payload or t.PreparedPayload(data=b"x", digest="0" * 64)
    return t.FrozenDelivery(
        job_ref="djb_" + "a" * 26,
        generation_ref="gen_" + "a" * 26,
        transport=transport,
        identity=identity,
        payload=payload,
        idempotency_key="k" * 64,
        attempt_no=1,
    )


def test_both_fakes_satisfy_the_protocol():
    for fake in (fakes.FakeTelegram(), fakes.FakeWhatsApp()):
        assert isinstance(fake, t.DeliveryTransport)
        for method in ("normalize", "prepare", "still_valid", "deliver"):
            assert callable(getattr(fake, method))
    assert fakes.FakeTelegram().name == "telegram" and fakes.FakeWhatsApp().name == "whatsapp"


def test_every_boundary_type_is_frozen():
    values = [
        t.DeliveryIntent(transport="whatsapp", identity=PHONE, content={"body": "b"}),
        t.PreparedPayload(data=b"x", digest="0" * 64),
        t.Skip(reason=t.SkipReason.PLATFORM_INELIGIBLE),
        _frozen(),
        t.DeliveryResult(kind=t.ResultKind.ACCEPTED),
    ]
    for value in values:
        field = dataclasses.fields(value)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, None)


def test_fake_telegram_marks_peer_kinds():
    norm = fakes.FakeTelegram().normalize
    assert norm("user:12345") == norm("private:12345") == "12345"
    assert len({norm("user:12345"), norm("group:12345"), norm("channel:12345")}) == 3
    with pytest.raises(ValueError):
        norm("chat:12345")


def test_fakes_record_transaction_state(conn):
    fake = fakes.FakeWhatsApp(conn=conn)
    intent = t.DeliveryIntent(transport="whatsapp", identity=PHONE, content={"body": "b"})
    with write_tx(conn):
        payload = fake.prepare(intent, NOW)
        assert fake.still_valid(payload, NOW) is True
    fake.deliver(_frozen(payload=payload))
    assert [(m, inside) for m, _, inside in fake.calls] == [
        ("prepare", True),
        ("still_valid", True),
        ("deliver", False),
    ]
    assert fake.sent == [PHONE] and fake.io_calls == []


def test_an_impure_still_valid_is_visible(conn):
    fake = fakes.ImpureFakeWhatsApp(conn=conn)
    payload = fake.prepare(t.DeliveryIntent("whatsapp", PHONE, {"body": "b"}), NOW)
    with write_tx(conn):
        fake.still_valid(payload, NOW)
    assert fake.io_calls == [("still_valid", True)]


def test_deliver_inside_a_transaction_is_refused_by_io_guard(conn):
    with write_tx(conn), pytest.raises(TransactionIOError):
        io_guard(conn)


def test_fake_script_outcomes():
    fake = fakes.FakeWhatsApp(
        script={
            PHONE: [
                "deliver",
                "transient_unsent",
                "permanent",
                "unknown",
                "raise",
                "crash_after_accept",
            ]
        }
    )
    kinds = [fake.deliver(_frozen()).kind for _ in range(4)]
    assert kinds == ["DELIVERED", "FAILED_TRANSIENT", "FAILED_PERMANENT", "OUTCOME_UNKNOWN"]
    with pytest.raises(RuntimeError):
        fake.deliver(_frozen())
    with pytest.raises(fakes.SimulatedCrash):
        fake.deliver(_frozen())
    assert not issubclass(fakes.SimulatedCrash, Exception)
    assert fake.sent == [PHONE, PHONE, PHONE]  # deliver, unknown, crash-after-accept transmitted
    assert fake.deliver(_frozen()).kind == "ACCEPTED"  # default


def test_fake_prepare_skip_and_window():
    closes = NOW + timedelta(hours=1)
    fake = fakes.FakeWhatsApp(
        script={PHONE: [("window_closes_at", closes)], "+61400000002": ["ineligible"]}
    )
    assert fake.prepare(t.DeliveryIntent("whatsapp", "+61400000002", {}), NOW) == t.Skip(
        t.SkipReason.PLATFORM_INELIGIBLE
    )
    payload = fake.prepare(t.DeliveryIntent("whatsapp", PHONE, {"body": "b"}), NOW)
    assert isinstance(payload, t.PreparedPayload)
    assert fake.still_valid(payload, NOW) is True
    assert fake.still_valid(payload, closes) is False


def test_reprs_hold_no_identity_or_payload():
    secret = "+61499999999"
    values = [
        t.DeliveryIntent(transport="whatsapp", identity=secret, content={"body": "CANARY"}),
        t.PreparedPayload(data=b"CANARY" + secret.encode(), digest="0" * 64),
        _frozen(identity=secret, payload=t.PreparedPayload(data=b"CANARY", digest="0" * 64)),
    ]
    for value in values:
        assert secret not in repr(value) and "CANARY" not in repr(value)
        assert "k" * 64 not in repr(value)


def test_result_kind_contract_is_documented():
    doc = " ".join((t.ResultKind.__doc__ or "").split())
    assert "no provider-side send occurred" in doc
    assert "idempotency" in doc and "OUTCOME_UNKNOWN" in doc
    proto = " ".join((t.DeliveryTransport.__doc__ or "").split())
    assert "no I/O of any kind" in proto


def test_delivery_result_refuses_an_unknown_kind():
    with pytest.raises(ValueError):
        t.DeliveryResult(kind="SENT")


def test_planted_failure_aborts_and_rolls_back(conn):
    w = fx.world(conn)
    fakes.plant_failure(conn, "campaign_events", "INSERT")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError, match="planted"), write_tx(conn):
        conn.execute("UPDATE delivery_jobs SET state = 'CANCELLED' WHERE id = ?", (w["job"],))
        conn.execute(
            "INSERT INTO campaign_events (event_ref, event_type, ts, payload) VALUES ('cev_x','x',?,'{}')",
            (fx.T0,),
        )
    assert conn.execute("SELECT state FROM delivery_jobs").fetchone()[0] == "PENDING"
    fakes.clear_planted(conn)
    assert (
        conn.execute("SELECT count(*) FROM sqlite_temp_master WHERE type='trigger'").fetchone()[0]
        == 0
    )


def test_fake_lock():
    lock = fakes.FakeLock()
    assert lock.held() is True
    lock.release()
    assert lock.held() is False
