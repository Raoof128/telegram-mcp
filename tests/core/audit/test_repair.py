"""comms v0.3 Task B21: audit repair through an ancestor-proving verifier (G7)."""

import json

import pytest

from comms.core.audit.anchor import COMMS_ANCHOR, read_anchor, write_anchor
from comms.core.audit.chain import COMMS, append_event, head
from comms.core.audit.integrity import is_degraded, latch_degraded
from comms.core.audit.repair import (
    RepairPlan,
    RepairRefused,
    audit_repair,
    verify_for_anchor_repair,
)
from comms.core.keys.slots import registry_public_for
from comms.core.storage.db import write_tx
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.audit.test_epochs import event
from tests.core.campaign_helpers import NOW


@pytest.fixture
def w(tmp_path):
    world = comms_world(tmp_path)
    for n in range(3):
        with world["writer"].transaction() as tx:
            tx.append("system.test_marker", payload={"count": n})
    # a failed refresh: the next event committed, the anchor still names the one before
    with write_tx(world["conn"]):
        append_event(world["conn"], COMMS, world["keys"].current(), event())
    latch_degraded(world["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    return world


def _verify(w):
    return verify_for_anchor_repair(
        w["conn"], COMMS, w["keys"].for_epoch, registry_public_for(w["conn"]), w["anchor"]
    )


def test_repair_advances_a_stale_but_ancestral_anchor_and_clears_latch(w):
    plan = _verify(w)
    assert isinstance(plan, RepairPlan) and (plan.anchor_seq, plan.head_seq) == (3, 4)
    audit_repair(w["writer"], plan)
    assert not is_degraded(w["conn"])
    current = head(w["conn"], COMMS)
    anchored = read_anchor(COMMS_ANCHOR, w["anchor"], w["keys"].for_epoch(current["chain_epoch"]))
    assert (anchored["chain_seq"], anchored["event_mac"]) == (
        current["chain_seq"],
        current["event_mac"],
    )
    kinds = [r[0] for r in w["conn"].execute("SELECT kind FROM audit_events ORDER BY chain_seq")]
    assert kinds[-1] == "admin.audit_repair"


def _refused(w, code):
    result = _verify(w)
    assert isinstance(result, RepairRefused) and result.code == code
    with pytest.raises(ValueError):
        audit_repair(w["writer"], result)
    assert is_degraded(w["conn"])


def test_repair_refuses_missing_anchor(w):
    w["anchor"].unlink()
    _refused(w, "ANCHOR_MISSING")


def test_repair_refuses_anchor_not_in_retained_chain(w):
    write_anchor(
        COMMS_ANCHOR,
        w["anchor"],
        w["keys"].for_epoch(1),
        chain_epoch=1,
        chain_seq=2,
        event_id="aev_" + "z" * 26,
        event_mac="f" * 64,
        now="2026-09-24T00:00:00.000000Z",
    )
    _refused(w, "ANCHOR_NOT_IN_CHAIN")


def test_repair_refuses_anchor_ahead_of_db(w):
    write_anchor(
        COMMS_ANCHOR,
        w["anchor"],
        w["keys"].for_epoch(1),
        chain_epoch=1,
        chain_seq=9,
        event_id="aev_" + "z" * 26,
        event_mac="f" * 64,
        now="2026-09-24T00:00:00.000000Z",
    )
    _refused(w, "ANCHOR_AHEAD")


def test_repair_refuses_unauthenticated_anchor(w):
    body = json.loads(w["anchor"].read_text())
    body["anchor_mac"] = "0" * 64
    w["anchor"].write_text(json.dumps(body))
    _refused(w, "ANCHOR_UNAUTHENTICATED")


def test_repair_refuses_if_any_link_fails(w):
    w["conn"].execute("DROP TRIGGER audit_events_append_only_u")
    w["conn"].execute("UPDATE audit_events SET payload = '{\"count\":99}' WHERE chain_seq = 4")
    w["conn"].commit()
    _refused(w, "CHAIN_INVALID")
