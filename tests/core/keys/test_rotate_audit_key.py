"""comms v0.3 Task B6: rotating audit-chain-key seals the epoch and opens the next (design §B.4, G8)."""

import json
import os

import pytest

from comms.core.audit import cutover
from comms.core.audit.anchor import CLEAN, COMMS_ANCHOR, derive_integrity
from comms.core.audit.chain import COMMS, ChainError, verify_chain
from comms.core.audit.verify_all import verify_all
from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active
from tests.core.audit.legacy_fixtures import comms_world, verify_keys
from tests.core.campaign_helpers import NOW


@pytest.fixture
def env(tmp_path):
    world = comms_world(tmp_path)
    with world["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
        tx.append("system.test_marker", payload={"count": 2})
    return world


def _rotate(env):
    return rot.rotate(
        env["writer"],
        env["store"],
        "audit-chain-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )


def _rows(conn):
    return conn.execute(
        "SELECT chain_epoch, chain_seq, kind, payload FROM audit_events ORDER BY chain_epoch, chain_seq"
    ).fetchall()


def test_rotation_seals_and_opens_an_epoch_whose_first_event_is_the_rotation(env):
    old_key = load_active(env["conn"], env["store"], "audit-chain-key")[0]
    version = _rotate(env)
    rows = _rows(env["conn"])
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (1, 1, "system.test_marker"),
        (1, 2, "system.test_marker"),
        (2, 1, "admin.key_rotation"),
    ]
    payload = json.loads(rows[-1][3])
    assert (payload["purpose"], payload["old_version"], payload["new_version"]) == (
        "audit-chain-key",
        1,
        version,
    )
    seal = (
        env["conn"]
        .execute("SELECT chain_epoch, chain_seq, reason FROM audit_checkpoints")
        .fetchall()
    )
    assert seal == [(1, 2, "EPOCH_SEAL")]
    assert env["keys"].for_epoch(1) == old_key != env["keys"].for_epoch(2)


def test_no_second_rotation_event_at_seq_2(env):
    _rotate(env)
    with env["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 3})
    kinds = [(r[0], r[1], r[2]) for r in _rows(env["conn"]) if r[0] == 2]
    assert kinds == [(2, 1, "admin.key_rotation"), (2, 2, "system.test_marker")]


def test_verify_uses_each_epochs_key(env):
    _rotate(env)
    _rotate(env)
    public_for = verify_keys(env).comms_public_for
    verify_chain(env["conn"], COMMS, env["keys"].for_epoch, public_for=public_for)
    with pytest.raises(ChainError):
        verify_chain(env["conn"], COMMS, lambda _e: env["keys"].for_epoch(3), public_for=public_for)
    assert (
        derive_integrity(
            env["conn"],
            COMMS,
            COMMS_ANCHOR,
            env["keys"].for_epoch,
            env["anchor"],
            public_for=public_for,
        )
        == CLEAN
    )


def test_old_mac_secret_kept_until_its_epoch_is_truncated(env):
    _rotate(env)
    rows = (
        env["conn"]
        .execute(
            "SELECT version, state FROM key_slots WHERE purpose = 'audit-chain-key' ORDER BY version"
        )
        .fetchall()
    )
    assert rows == [(1, "RETIRED"), (2, "ACTIVE")]
    assert env["store"].versions("audit-chain-key") == [
        1,
        2,
    ]  # the retired secret still verifies epoch 1


def test_verify_all_is_green_across_a_rotated_comms_chain(tmp_path):
    world = comms_world(tmp_path, bearer=True)
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    rot.rotate(
        world["writer"],
        world["store"],
        "audit-chain-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    report = verify_all(world["conn"], world["port"].conn, verify_keys(world))
    assert report.problems == () and report.ok
