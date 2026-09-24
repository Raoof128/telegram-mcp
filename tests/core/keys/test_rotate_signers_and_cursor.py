"""comms v0.3 Task B7: checkpoint-key and cursor-key rotation consequences (design §B.4)."""

import json
import os

import pytest

from comms.core.audit.chain import COMMS, insert_checkpoint, verify_checkpoints
from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active, registry_public_for
from comms.core.storage.db import write_tx
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW


@pytest.fixture
def env(tmp_path):
    world = comms_world(tmp_path)
    with world["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    return world


def _rotate(env, purpose):
    return rot.rotate(
        env["writer"], env["store"], purpose, material=os.urandom(32), prove=lambda m: None, now=NOW
    )


def _checkpoint(env):
    key = load_active(env["conn"], env["store"], "audit-checkpoint-key")[0]
    with write_tx(env["conn"]):
        return insert_checkpoint(
            env["conn"], COMMS, key, now="2026-09-24T00:00:00.000000Z", reason="PERIODIC"
        )


def test_checkpoint_rotation_new_key_id_old_public_kept(env):
    before = _checkpoint(env)
    _rotate(env, "audit-checkpoint-key")
    after = _checkpoint(env)
    assert before["signing_key_id"] != after["signing_key_id"]
    verify_checkpoints(env["conn"], COMMS, registry_public_for(env["conn"]))  # both still verify
    states = dict(
        env["conn"].execute("SELECT key_id, trust_state FROM verification_keys").fetchall()
    )
    assert states[before["signing_key_id"]] == "TRUSTED_RETIRED"
    assert states[after["signing_key_id"]] == "ACTIVE"
    assert env["store"].versions("audit-checkpoint-key") == [2]  # the old private half is gone


def _stub_context_tables(conn):
    with write_tx(conn):
        conn.execute("CREATE TABLE ctx_handles (ref TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE cursors (ref TEXT PRIMARY KEY, ctx_ref TEXT NOT NULL REFERENCES ctx_handles(ref))"
        )
        conn.executemany("INSERT INTO ctx_handles VALUES (?)", [("ctx_a",), ("ctx_b",)])
        conn.executemany(
            "INSERT INTO cursors VALUES (?, ?)", [("cur_a", "ctx_a"), ("cur_b", "ctx_b")]
        )


def test_cursor_rotation_invalidates_every_cursor_and_ctx_handle(env):
    _stub_context_tables(env["conn"])
    _rotate(env, "cursor-key")
    counts = [
        env["conn"].execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("cursors", "ctx_handles")
    ]
    assert counts == [0, 0]
    (payload,) = [
        json.loads(r[0])
        for r in env["conn"].execute(
            "SELECT payload FROM audit_events WHERE kind = 'admin.key_rotation'"
        )
    ]
    assert payload["purpose"] == "cursor-key"


def test_cursor_rotation_before_context_tables_exist_invalidates_nothing_and_succeeds(env):
    assert _rotate(env, "cursor-key") == 1


def test_cursor_old_secret_destroyed_immediately(env):
    _rotate(env, "cursor-key")
    _rotate(env, "cursor-key")
    assert env["store"].versions("cursor-key") == [2]
    assert env["conn"].execute(
        "SELECT version, state FROM key_slots WHERE purpose = 'cursor-key' ORDER BY version"
    ).fetchall() == [(1, "DESTROYED"), (2, "ACTIVE")]
