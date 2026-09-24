"""comms v0.3 Task C15 (A42, G11): a provider request key is persisted on the attempt in the
claim transaction, before the call, under scoped uniqueness; a collision is never sent."""

import pytest
import sqlcipher3

from comms.core.delivery import freeze
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.delivery.operations import retry_failed
from comms.core.storage.db import write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, job_states, person, ready

TG = frozenset({"telegram"})


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _campaign(conn, transport, tg="user:1"):
    rcp, _ = person(conn, tg=tg)
    cmp = ready(conn, {"recipients": [rcp]}, TG)
    freeze.send(conn, cmp, {"telegram": transport}, now=NOW)
    return cmp


def _run(conn, transport, cmp):
    return Engine(conn, {"telegram": transport}, clock=lambda: NOW).execute(
        ExecutorLease(fakes.FakeLock()), cmp
    )


def test_random_id_persisted_on_the_attempt_before_the_call(conn):
    transport = fakes.KeyedTelegram(conn)
    cmp = _campaign(conn, transport)
    _run(conn, transport, cmp)
    ((key, actor),) = transport.seen_keys
    assert key.startswith("k-") and actor == "telegram_user"


def test_a_transport_without_a_key_stores_none(conn):
    transport = fakes.FakeTelegram(conn=conn)
    cmp = _campaign(conn, transport)
    _run(conn, transport, cmp)
    row = conn.execute(
        "SELECT provider_request_key, transport_actor FROM delivery_attempts"
    ).fetchone()
    assert tuple(row) == (None, None)


def test_random_id_collision_detected_and_never_sent(conn):
    first = fakes.KeyedTelegram(conn, key_of=lambda idem: "same")
    _run(conn, first, _campaign(conn, first, tg="user:1"))
    second = fakes.KeyedTelegram(conn, key_of=lambda idem: "same")
    cmp = _campaign(conn, second, tg="user:2")
    report = _run(conn, second, cmp)
    assert second.sent == [] and second.seen_keys == []
    assert report.outcomes == {"FAILED_PERMANENT": 1} and set(job_states(conn, cmp).values()) == {
        "FAILED_PERMANENT"
    }
    code = conn.execute(
        "SELECT outcome_code, provider_request_key FROM delivery_attempts ORDER BY id DESC"
    ).fetchone()
    assert tuple(code) == ("RANDOM_ID_COLLISION", None)


def test_a_retried_job_reuses_its_own_key(conn):
    transport = fakes.KeyedTelegram(conn, script={"1": ["transient_unsent"]})
    cmp = _campaign(conn, transport)
    _run(conn, transport, cmp)
    retry_failed(conn, cmp, now=NOW)
    _run(conn, transport, cmp)
    assert len(transport.seen_keys) == 2 and transport.seen_keys[0] == transport.seen_keys[1]
    assert set(job_states(conn, cmp).values()) == {"ACCEPTED"}


def test_the_database_refuses_a_cross_job_key_and_a_rebound_key(conn):
    transport = fakes.KeyedTelegram(conn)
    _run(conn, transport, _campaign(conn, transport, tg="user:1"))
    _run(conn, transport, _campaign(conn, transport, tg="user:2"))
    ids = [r[0] for r in conn.execute("SELECT id FROM delivery_attempts ORDER BY id")]
    key = conn.execute(
        "SELECT provider_request_key FROM delivery_attempts WHERE id = ?", (ids[0],)
    ).fetchone()[0]
    for sql, args in (
        ("UPDATE delivery_attempts SET provider_request_key = ? WHERE id = ?", (key, ids[1])),
        ("UPDATE delivery_attempts SET provider_request_key = 'other' WHERE id = ?", (ids[0],)),
    ):
        with pytest.raises(sqlcipher3.IntegrityError), write_tx(conn):
            conn.execute(sql, args)
