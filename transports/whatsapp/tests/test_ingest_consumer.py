import hashlib
import json
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from apps.ingest.consumer import drain_once
from apps.ingest.queue_client import FakeQueue
from whatsvault.crypto import sealed as S
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ingest import dlq


def _dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return v, c


def _kp():
    p = X25519PrivateKey.generate()
    return p.private_bytes_raw(), p.public_key().public_bytes_raw()


def _sealed(pub, key_id, value):
    pt = json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()
    return S.seal(pub, pt, recipient_key_id=key_id, event_id_hash=hashlib.sha256(pt).digest())


INBOUND = {
    "metadata": {"phone_number_id": "PN1"},
    "contacts": [{"wa_id": "61999", "profile": {"name": "Mona"}}],
    "messages": [
        {
            "from": "61999",
            "id": "wamid.M1",
            "timestamp": "1700000000",
            "type": "text",
            "text": {"body": "hello"},
        }
    ],
}


def test_valid_inbound_committed_indexed_window(tmp_path):
    v, c = _dbs(tmp_path)
    priv, pub = _kp()
    env = _sealed(pub, 1, INBOUND)
    q = FakeQueue([env])
    drain_once(q, v, c, lambda k: priv if k == 1 else None, key_health=set(), now_ms=1)
    assert v.execute("SELECT COUNT(*) FROM messages WHERE window_eligible=1").fetchone()[0] == 1
    assert c.execute("SELECT last_inbound_ms FROM conversation_windows").fetchone()[0] == 1700000000 * 1000
    assert v.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0] == 1  # post-commit index
    assert env in q._acked


def test_duplicate_absorbed(tmp_path):
    v, c = _dbs(tmp_path)
    priv, pub = _kp()
    env = _sealed(pub, 1, INBOUND)
    q = FakeQueue([env, env])
    drain_once(q, v, c, lambda k: priv, key_health=set(), now_ms=1)
    assert v.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert len(q._acked) == 2  # second deduped, still acked


def test_message_plus_two_statuses_all_durable_before_ack(tmp_path):
    v, c = _dbs(tmp_path)
    priv, pub = _kp()
    value = dict(INBOUND)
    value["statuses"] = [
        {"id": "wamid.M0", "status": "delivered", "timestamp": "1700000001", "recipient_id": "61999"},
        {"id": "wamid.M0", "status": "read", "timestamp": "1700000002", "recipient_id": "61999"},
    ]
    env = _sealed(pub, 1, value)
    q = FakeQueue([env])
    drain_once(q, v, c, lambda k: priv, key_health=set(), now_ms=1)
    assert v.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert v.execute("SELECT COUNT(*) FROM message_status_events").fetchone()[0] == 2
    assert env in q._acked


def test_key_unavailable_not_acked_redelivered(tmp_path):
    v, c = _dbs(tmp_path)
    priv, pub = _kp()
    env = _sealed(pub, 2, INBOUND)
    q = FakeQueue([env])
    drain_once(q, v, c, lambda k: None, key_health=set(), now_ms=1)
    assert env not in q._acked and env in q._pending


def test_systemic_wrong_key_trips_breaker(tmp_path):
    v, c = _dbs(tmp_path)
    priv, pub = _kp()
    other, _ = _kp()
    env = _sealed(pub, 1, INBOUND)
    q = FakeQueue([env])
    drain_once(q, v, c, lambda k: other, key_health=set(), now_ms=1)  # wrong key, cold health
    assert dlq.state(v) == "OPEN" and env not in q._acked
    q2 = FakeQueue([env])
    res = drain_once(q2, v, c, lambda k: priv, key_health=set(), now_ms=2)
    assert res.get("circuit") == "OPEN" and len(q2._acked) == 0  # breaker open -> no lease


def test_crash_after_commit_absorbed_on_redelivery(tmp_path):
    v, c = _dbs(tmp_path)
    priv, pub = _kp()
    env = _sealed(pub, 1, INBOUND)
    q = FakeQueue([env])
    calls = {"n": 0}

    def fault():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash after commit, before ACK")

    drain_once(q, v, c, lambda k: priv, key_health=set(), now_ms=1, _fault_after_commit=fault)
    assert env not in q._acked and env in q._pending  # committed but not acked
    drain_once(q, v, c, lambda k: priv, key_health=set(), now_ms=2)
    assert v.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1  # dedup absorbed
    assert env in q._acked
