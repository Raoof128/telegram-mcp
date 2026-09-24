"""comms v0.3 Task B9: backup-signer trust states separate verification from import trust (A11)."""

import json
import os

import pytest
import sqlcipher3

from comms.core.keys import rotate as rot
from comms.core.keys.signers import can_verify, listed_signers, mark_signer, require_import_trust
from comms.core.keys.slots import KeySlotError, active_version
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW


@pytest.fixture
def env(tmp_path):
    world = comms_world(tmp_path)
    for _ in range(2):
        rot.rotate(
            world["writer"],
            world["store"],
            "backup-key",
            material=os.urandom(32),
            prove=lambda m: None,
            now=NOW,
        )
    old, new = (
        r[0]
        for r in world["conn"].execute(
            "SELECT key_id FROM key_slots WHERE purpose = 'backup-key' ORDER BY version"
        )
    )
    world["old"], world["new"] = old, new
    return world


def _state(env, key_id):
    return (
        env["conn"]
        .execute("SELECT trust_state FROM verification_keys WHERE key_id = ?", (key_id,))
        .fetchone()[0]
    )


def test_normal_rotation_leaves_old_signer_trusted_retired(env):
    assert (_state(env, env["old"]), _state(env, env["new"])) == ("TRUSTED_RETIRED", "ACTIVE")
    require_import_trust(env["conn"], env["old"])  # a normal retirement still imports
    assert can_verify(env["conn"], env["old"])


def test_compromise_marks_verification_only_and_import_refuses(env):
    mark_signer(env["writer"], env["old"], "VERIFICATION_ONLY", now=NOW)
    assert _state(env, env["old"]) == "VERIFICATION_ONLY"
    assert can_verify(env["conn"], env["old"])
    with pytest.raises(KeySlotError, match="import"):
        require_import_trust(env["conn"], env["old"])
    (payload,) = [
        json.loads(r[0])
        for r in env["conn"].execute(
            "SELECT payload FROM audit_events WHERE kind = 'admin.signer_trust'"
        )
    ]
    assert payload == {
        "key_id": env["old"],
        "from_state": "TRUSTED_RETIRED",
        "to_state": "VERIFICATION_ONLY",
    }


def test_revoked_signer_refuses_import_and_is_still_listed_for_forensics(env):
    mark_signer(env["writer"], env["old"], "REVOKED", now=NOW)
    assert not can_verify(env["conn"], env["old"])
    with pytest.raises(KeySlotError, match="import"):
        require_import_trust(env["conn"], env["old"])
    listed = {
        row["key_id"]: row["trust_state"] for row in listed_signers(env["conn"], "backup-key")
    }
    assert listed == {env["old"]: "REVOKED", env["new"]: "ACTIVE"}


def test_state_transitions_are_one_way_toward_less_trust(env):
    mark_signer(env["writer"], env["old"], "REVOKED", now=NOW)
    for back in ("VERIFICATION_ONLY", "TRUSTED_RETIRED", "ACTIVE"):
        with pytest.raises(KeySlotError, match="one-way"):
            mark_signer(env["writer"], env["old"], back, now=NOW)
    with pytest.raises(sqlcipher3.IntegrityError, match="one-way"):
        env["conn"].execute(
            "UPDATE verification_keys SET trust_state = 'ACTIVE' WHERE key_id = ?", (env["old"],)
        )
    env["conn"].rollback()


def test_the_active_signer_can_be_revoked_but_stays_active_in_its_slot_until_rotation(env):
    mark_signer(env["writer"], env["new"], "REVOKED", now=NOW)
    assert active_version(env["conn"], "backup-key")[1] == env["new"]
    with pytest.raises(KeySlotError, match="import"):
        require_import_trust(env["conn"], env["new"])


def test_unknown_key_ids_are_refused(env):
    with pytest.raises(KeySlotError):
        mark_signer(env["writer"], "ed25519:sha256:" + "0" * 64, "REVOKED", now=NOW)
    assert not can_verify(env["conn"], "ed25519:sha256:" + "0" * 64)


def test_rotating_away_from_a_revoked_signer_keeps_it_revoked(env):
    mark_signer(env["writer"], env["new"], "REVOKED", now=NOW)
    rot.rotate(
        env["writer"],
        env["store"],
        "backup-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    assert _state(env, env["new"]) == "REVOKED"
