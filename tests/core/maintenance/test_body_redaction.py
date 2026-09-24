"""comms v0.3 Task B19: campaign-body redaction; digests kept; schema v3 rebuild (A15, O1)."""

import json
import os
from datetime import timedelta

import pytest
import sqlcipher3

from comms.core.delivery import freeze
from comms.core.delivery.commitment import commit_context, verify_commitment
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.keys import rotate as rot
from comms.core.maintenance.redaction import redact_campaign_bodies
from comms.core.storage.db import open_comms_db, write_tx
from comms.core.storage.migrations import MIGRATIONS, migrate
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW, campaign_state, person, ready

BODY = "Nowruz canary 7f3a body"
LATER = NOW + timedelta(days=40)


def _redact(conn, *, days=30, at=LATER):
    with write_tx(conn):
        return redact_campaign_bodies(conn, cutoff=at - timedelta(days=days), now=at)


def _complete(conn, tx, phone="+61400000001", *, audited=None):
    rcp, _ = person(conn, phone=phone)
    cmp = ready(conn, {"recipients": [rcp]}, body=BODY)
    gen = freeze.send(conn, cmp, tx, now=NOW, audited=audited)
    Engine(conn, tx, clock=lambda: NOW).execute(ExecutorLease(fakes.FakeLock()), cmp)
    assert campaign_state(conn, cmp)[0] == "COMPLETE"
    return cmp, gen


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def tx(conn):
    return {"whatsapp": fakes.FakeWhatsApp(conn=conn)}


def _objects(conn):
    return sorted(
        (r[0], r[1])
        for r in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('index', 'trigger')"
        )
    )


JOB_COLUMNS = (
    "id, ref, generation_id, transport, identity_id, idempotency_key, payload,"
    " payload_digest, skip_reason, state, attempt_count"
)
PRESERVED = (
    ("delivery_jobs", JOB_COLUMNS),
    ("job_origins", "*"),
    ("generations", "id, ref, content, snapshot_digest"),
)


def test_v3_rebuild_preserves_every_row_index_and_trigger(tmp_path):
    conn = open_comms_db(tmp_path / "v2.db", fx.KEY)
    migrate(conn, MIGRATIONS[:2])
    w = fx.world(conn)
    before = {
        t: conn.execute(f"SELECT {cols} FROM {t} ORDER BY 1").fetchall() for t, cols in PRESERVED
    }
    before_objects = _objects(conn)
    assert migrate(conn, MIGRATIONS) == 3
    for table, cols in PRESERVED:
        assert conn.execute(f"SELECT {cols} FROM {table} ORDER BY 1").fetchall() == before[table], (
            table
        )
    replaced = {("trigger", "audit_events_append_only_d")}  # by the truncation-only guard (B18)
    assert set(before_objects) - replaced <= set(_objects(conn))  # every other one survives
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    fx.origin(conn, w["job"], w["cp_ref"])  # the recreated origin trigger still guards inserts


def test_redaction_clears_payload_and_generation_content_keeps_digests(conn, tx):
    cmp, gen = _complete(conn, tx)
    digests = conn.execute(
        "SELECT g.snapshot_digest, j.payload_digest FROM generations g JOIN delivery_jobs j ON j.generation_id = g.id WHERE g.ref = ?",
        (gen,),
    ).fetchone()
    assert _redact(conn) == 1
    row = conn.execute(
        "SELECT g.content, g.redacted_at, g.snapshot_digest, j.payload, j.payload_digest, j.redacted_at"
        " FROM generations g JOIN delivery_jobs j ON j.generation_id = g.id WHERE g.ref = ?",
        (gen,),
    ).fetchone()
    assert row[0] == "{}" and row[1] is not None and row[2] == digests[0]
    assert row[3] is None and row[4] == digests[1] and row[5] is not None
    assert (
        json.loads(
            conn.execute("SELECT content FROM campaigns WHERE ref = ?", (cmp,)).fetchone()[0]
        )
        == {}
    )


def test_redaction_only_for_complete_or_cancelled_past_policy(conn, tx):
    _complete(conn, tx)
    assert _redact(conn, days=60) == 0  # completed, but inside the retention window
    rcp, _ = person(conn, phone="+61400000009")
    sending = ready(conn, {"recipients": [rcp]}, body=BODY)
    freeze.send(conn, sending, tx, now=NOW)  # SENDING, never executed
    assert _redact(conn) == 1  # only the completed one
    pending = conn.execute(
        "SELECT count(*) FROM delivery_jobs WHERE payload IS NOT NULL AND redacted_at IS NULL"
    ).fetchone()[0]
    assert pending == 1


def test_any_other_binding_update_still_refused(conn, tx):
    _complete(conn, tx)
    _redact(conn)
    for sql in (
        "UPDATE delivery_jobs SET payload_digest = '" + "0" * 64 + "'",
        "UPDATE delivery_jobs SET redacted_at = 'again'",
        "UPDATE generations SET content = 'x', redacted_at = 'again'",
        "UPDATE generations SET snapshot_digest = '" + "0" * 64 + "'",
    ):
        with pytest.raises(sqlcipher3.IntegrityError, match="frozen"):
            conn.execute(sql)
    with pytest.raises(sqlcipher3.IntegrityError, match="frozen"):
        conn.execute("UPDATE delivery_jobs SET payload = X'00'")


def test_commitment_reverifies_after_redaction(tmp_path):
    world = comms_world(tmp_path)
    rot.rotate(
        world["writer"],
        world["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    tx = {"whatsapp": fakes.FakeWhatsApp(conn=world["conn"])}
    _cmp, gen = _complete(
        world["conn"], tx, audited=commit_context(world["writer"], world["store"])
    )
    assert _redact(world["conn"]) == 1
    assert verify_commitment(world["conn"], world["store"], gen)


def test_redacted_logical_rows_hold_no_body(conn, tx):
    _complete(conn, tx)
    _redact(conn)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table}"):
            for value in row:
                text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
                assert "canary" not in text, table


def test_secure_delete_is_on_for_comms_db(conn):
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_canaries_absent_from_db_files_after_redaction_and_checkpoint(conn, tx, tmp_path):
    """An encryption check only: the file is ciphertext either way. O1's proof is the logical-row test."""
    _complete(conn, tx)
    _redact(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for path in tmp_path.glob("comms.db*"):
        assert b"canary" not in path.read_bytes()
