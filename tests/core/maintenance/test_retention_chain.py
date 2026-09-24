"""comms v0.3 Task B18: comms chain truncation only behind a verified root; the lineage survives."""

import os

import pytest
import sqlcipher3

from comms.core.audit import cutover
from comms.core.audit.chain import COMMS, insert_checkpoint
from comms.core.audit.verify_all import verify_all
from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active
from comms.core.maintenance.retention import RetentionPolicy, run_retention
from comms.core.storage.db import write_tx
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.core.audit.legacy_fixtures import CHAIN_KEY, comms_world, public_for, verify_keys
from tests.core.campaign_helpers import NOW

KEEP = {
    "exposure_ledger_days": 3650,
    "receipt_days": 3650,
    "message_ref_days": 3650,
    "audit_events_days": 3650,
    "campaign_body_days": 3650,
    "identity_retention_days": 3650,
}
OLD = "2026-09-01T00:00:00.000000Z"  # the periodic checkpoint's stamp


def _marker(world, n):
    with world["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": n})


def _checkpoint(world, stamp=OLD):
    key = load_active(world["conn"], world["store"], "audit-checkpoint-key")[0]
    with write_tx(world["conn"]):
        return insert_checkpoint(world["conn"], COMMS, key, now=stamp, reason="PERIODIC")


@pytest.fixture
def world(tmp_path):
    w = comms_world(tmp_path, bearer=True)
    cutover.run_cutover(w["conn"], w["port"], w["writer"], now=NOW)  # comms seq 1, 2
    _marker(w, 3)
    w["root"] = _checkpoint(w)  # at comms seq 3
    _marker(w, 4)
    w["legacy"] = TelegramLegacyRetention(w["port"].conn, CHAIN_KEY, public_for)
    return w


def _run(world, days=10):
    policy = RetentionPolicy(**{**KEEP, "audit_events_days": days})
    return run_retention(
        world["conn"], world["legacy"], policy, world["writer"], now=NOW, store=world["store"]
    )


def _seqs(world):
    """Retained events, apart from the maintenance event each retention run appends (B20)."""
    rows = world["conn"].execute(
        "SELECT chain_epoch, chain_seq FROM audit_events"
        " WHERE kind <> 'maintenance.retention_purge' ORDER BY 1, 2"
    )
    return [tuple(r) for r in rows]


def test_deletes_only_events_strictly_before_the_root_row(world):
    report = _run(world)
    assert report.phases["comms_chain"] == 2
    assert _seqs(world) == [(1, 3), (1, 4)]


def test_root_and_its_row_are_kept(world):
    report = _run(world)
    assert report.roots["comms"] == world["root"]["checkpoint_ref"]
    assert (1, 3) in _seqs(world)
    kept = world["conn"].execute(
        "SELECT count(*) FROM audit_checkpoints WHERE checkpoint_ref = ?",
        (world["root"]["checkpoint_ref"],),
    )
    assert kept.fetchone()[0] == 1


def test_verify_after_truncation_reports_truncated_at_verified_checkpoint(world):
    _run(world)
    report = verify_all(world["conn"], world["port"].conn, verify_keys(world))
    assert report.problems == () and report.ok
    assert report.comms_root == world["root"]["checkpoint_ref"]


def test_a_forged_root_after_truncation_fails_verification(world):
    _run(world)
    world["conn"].execute("DROP TRIGGER audit_checkpoints_append_only_u")
    world["conn"].execute(
        "UPDATE audit_checkpoints SET signature = ? WHERE checkpoint_ref = ?",
        ("00" * 64, world["root"]["checkpoint_ref"]),
    )
    world["conn"].commit()
    report = verify_all(world["conn"], world["port"].conn, verify_keys(world))
    assert not report.ok and "COMMS_CHAIN_INVALID" in report.problems


def test_ad_hoc_delete_refused_by_trigger(world):
    for table in ("audit_events", "audit_checkpoints"):
        with pytest.raises(sqlcipher3.IntegrityError, match="append-only"):
            world["conn"].execute(f"DELETE FROM {table}")
        world["conn"].rollback()


def test_lineage_record_survives_truncation_of_epoch_1(world):
    rot.rotate(
        world["writer"],
        world["store"],
        "audit-chain-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    _marker(world, 5)
    root = _checkpoint(world, stamp="2026-09-05T00:00:00.000000Z")  # inside epoch 2
    _marker(world, 6)
    report = _run(world)
    assert report.roots["comms"] == root["checkpoint_ref"]
    assert all(epoch == 2 for epoch, _seq in _seqs(world))  # epoch 1 is gone entirely
    assert world["conn"].execute("SELECT count(*) FROM audit_lineage").fetchone()[0] == 1
    verified = verify_all(world["conn"], world["port"].conn, verify_keys(world))
    assert verified.problems == () and verified.ok


def test_no_eligible_root_truncates_nothing(world):
    report = _run(world, days=40)  # the cutoff precedes every checkpoint
    assert report.phases["comms_chain"] == 0 and report.roots["comms"] is None
    assert len(_seqs(world)) == 4
