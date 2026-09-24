"""comms v0.3 Task B5: staged key rotation — stage → prove → activate — with orphans (design §B.4)."""

import json
import os

import pytest

from comms.core.audit.anchor import COMMS_ANCHOR, read_anchor
from comms.core.audit.chain import COMMS, head
from comms.core.audit.integrity import AuditIntegrityDegraded, latch_degraded
from comms.core.audit.writer import AuditWriter, SlotChainKeys
from comms.core.keys import rotate as rot
from comms.core.keys.purposes import PURPOSES
from comms.core.keys.slots import (
    KeySlotError,
    KeySlotStore,
    bootstrap_comms_audit_keys,
    load_active,
)
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

# The purposes the generic protocol rotates: new_id / invalidate, HMAC or Ed25519.
GENERIC = sorted(
    name
    for name, p in PURPOSES.items()
    if p.rotation in {"new_id", "invalidate"} and p.kind in {"hmac", "ed25519"}
)


@pytest.fixture
def env(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    adir = tmp_path / "anchor"
    adir.mkdir(mode=0o700)
    os.chmod(adir, 0o700)
    keys = SlotChainKeys(conn, store)
    writer = AuditWriter(conn, keys, adir / "head.anchor", clock=lambda: NOW)
    return {
        "conn": conn,
        "store": store,
        "keys": keys,
        "writer": writer,
        "anchor": adir / "head.anchor",
    }


def _rotate(
    env, purpose="audit-checkpoint-key", *, prove=lambda m: None, consequence=None, crash_at=None
):
    return rot.rotate(
        env["writer"],
        env["store"],
        purpose,
        material=os.urandom(32),
        prove=prove,
        consequence=consequence or (lambda tx, old, new: False),
        now=NOW,
        crash_at=crash_at,
    )


def _events(conn, kind="admin.key_rotation"):
    return [
        json.loads(r[0])
        for r in conn.execute("SELECT payload FROM audit_events WHERE kind = ?", (kind,))
    ]


def test_failed_proof_leaves_the_old_key_active_and_an_orphan(env):
    before = load_active(env["conn"], env["store"], "audit-checkpoint-key")

    def refuse(material):
        raise ValueError("proof failed")

    with pytest.raises(ValueError, match="proof failed"):
        _rotate(env, prove=refuse)
    assert load_active(env["conn"], env["store"], "audit-checkpoint-key") == before
    assert rot.find_orphans(env["conn"], env["store"]) == {"audit-checkpoint-key": [2]}
    assert _events(env["conn"]) == []


def test_crash_before_tx_leaves_an_orphan_that_doctor_reports_and_next_run_cleans(env):
    with pytest.raises(rot.RotationCrash):
        _rotate(env, crash_at="before_tx")
    assert rot.find_orphans(env["conn"], env["store"]) == {"audit-checkpoint-key": [2]}
    version = _rotate(env)
    assert version == 2  # the orphan was cleaned, then a fresh version 2 was staged and activated
    assert rot.find_orphans(env["conn"], env["store"]) == {}
    assert env["conn"].execute(
        "SELECT version, state FROM key_slots WHERE purpose = 'audit-checkpoint-key' ORDER BY version"
    ).fetchall() == [(1, "DESTROYED"), (2, "ACTIVE")]


def test_crash_after_tx_selects_the_new_version(env):
    old = load_active(env["conn"], env["store"], "audit-checkpoint-key")
    with pytest.raises(rot.RotationCrash):
        _rotate(env, crash_at="after_tx")
    material, key_id = load_active(env["conn"], env["store"], "audit-checkpoint-key")
    assert (material, key_id) != old
    assert len(_events(env["conn"])) == 1


def test_rotation_refused_while_degraded(env):
    latch_degraded(env["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    with pytest.raises(AuditIntegrityDegraded):
        _rotate(env)
    assert rot.find_orphans(env["conn"], env["store"]) == {}  # refused before staging


@pytest.mark.parametrize(
    "purpose",
    [
        "principal-key",
        "privacy-key",
        "disclosure-key",
        "consent-approval-key",
        "consent-transport-key",
    ],
)
def test_refused_purposes_raise(env, purpose):
    with pytest.raises(KeySlotError, match="retired"):
        _rotate(env, purpose)


def test_purposes_with_their_own_protocol_are_not_rotated_here(env):
    for purpose in ("comms-db-key", "telegram-bot-token"):  # audit-chain-key: B6 (seal_epoch)
        with pytest.raises(KeySlotError, match="own rotation"):
            _rotate(env, purpose)


def test_every_rotation_is_anchored(env):
    for _ in range(3):
        _rotate(env)
        current = head(env["conn"], COMMS)
        anchored = read_anchor(
            COMMS_ANCHOR, env["anchor"], env["keys"].for_epoch(current["chain_epoch"])
        )
        assert (anchored["chain_seq"], anchored["event_mac"]) == (
            current["chain_seq"],
            current["event_mac"],
        )


@pytest.mark.parametrize("purpose", GENERIC)
def test_exactly_one_rotation_event_per_rotation(env, purpose):
    for _ in range(2):  # a purpose not bootstrapped starts from version 0
        _rotate(env, purpose)
    payloads = [p for p in _events(env["conn"]) if p["purpose"] == purpose]
    assert len(payloads) == 2
    assert all(p["new_version"] == p["old_version"] + 1 for p in payloads)


def test_a_consequence_that_appends_its_own_event_suppresses_the_generic_one(env):
    def own(tx, old, new):
        tx.append("system.test_marker", payload={"count": new})
        return True

    _rotate(env, consequence=own)
    assert _events(env["conn"]) == []
    assert _events(env["conn"], "system.test_marker") == [{"count": 2}]


def test_signers_keep_their_old_public_half_as_trusted_retired(env):
    _rotate(env)
    rows = (
        env["conn"]
        .execute(
            "SELECT trust_state FROM verification_keys WHERE purpose = 'audit-checkpoint-key' ORDER BY activated_at, trust_state"
        )
        .fetchall()
    )
    assert sorted(r[0] for r in rows) == ["ACTIVE", "TRUSTED_RETIRED"]


def test_an_epoch_sealing_rotation_takes_no_caller_consequence(env):
    with pytest.raises(KeySlotError, match="no other consequence"):
        _rotate(env, "audit-chain-key", consequence=lambda tx, old, new: False)
