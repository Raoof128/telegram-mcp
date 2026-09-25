"""comms v0.3 Task B21: retention fails closed on a bad root; no eligible root is 'blocked'."""

from datetime import timedelta

import pytest

from comms.core.audit import cutover
from comms.core.audit.chain import COMMS, insert_checkpoint
from comms.core.audit.integrity import is_degraded
from comms.core.audit.writer import AuditWriter
from comms.core.keys.slots import load_active
from comms.core.maintenance.retention import RetentionFailed, RetentionPolicy, run_retention
from comms.core.storage.db import write_tx
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.core.audit.legacy_fixtures import CHAIN_KEY, comms_world, public_for
from tests.core.campaign_helpers import NOW

POLICY = RetentionPolicy(
    exposure_ledger_days=3650,
    receipt_days=3650,
    message_ref_days=3650,
    audit_events_days=10,
    campaign_body_days=3650,
    identity_retention_days=3650,
)


@pytest.fixture
def w(tmp_path):
    world = comms_world(tmp_path, bearer=True)
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    world["legacy"] = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    return world


def _count(w):
    return w["conn"].execute("SELECT count(*) FROM audit_events").fetchone()[0]


def _run(w):
    return run_retention(w["conn"], w["legacy"], POLICY, w["writer"], now=NOW, store=w["store"])


def test_root_mac_mismatch_latches_degraded_and_deletes_nothing(w):
    with w["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    key = load_active(w["conn"], w["store"], "audit-checkpoint-key")[0]
    with write_tx(w["conn"]):
        insert_checkpoint(
            w["conn"], COMMS, key, now="2026-09-01T00:00:00.000000Z", reason="PERIODIC"
        )
    w["conn"].execute("DROP TRIGGER audit_events_append_only_u")
    w["conn"].execute("UPDATE audit_events SET payload = '{\"count\":7}' WHERE chain_seq = 3")
    w["conn"].commit()
    before = _count(w)
    with pytest.raises(RetentionFailed):
        _run(w)
    assert is_degraded(w["conn"]) and _count(w) == before


def test_no_eligible_root_records_blocked_not_failure(w):
    earlier = AuditWriter(w["conn"], w["keys"], w["anchor"], clock=lambda: NOW - timedelta(days=30))
    with earlier.transaction() as tx:  # an event old enough to be due, and no checkpoint at all
        tx.append("system.test_marker", payload={"count": 1})
    report = _run(w)
    assert report.phases["comms_chain"] == 0 and report.roots["comms"] is None
    assert report.outcome == "blocked"
    assert not is_degraded(w["conn"])


def test_nothing_due_is_ok(w):
    assert _run(w).outcome == "ok"  # every event is newer than the cutoff
