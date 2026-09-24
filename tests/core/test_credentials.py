"""comms v0.3 Task B13: provider credentials rotate stage → prove → activate → re-check (A13, O2)."""

import json
import logging

import pytest

from comms.core.audit.integrity import AuditIntegrityDegraded, latch_degraded
from comms.core.credentials import (
    CredentialCheckFailed,
    active_credential,
    credential_status,
    revoke_credential,
    rotate_credential,
)
from comms.core.keys.secrets import FileSecretStore
from comms.core.keys.slots import KeySlotError
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

PURPOSE = "telegram-bot-token"
OLD, NEW = b"111111:old-token-canary-a", b"222222:new-token-canary-b"


@pytest.fixture
def env(tmp_path):
    world = comms_world(tmp_path)
    world["secrets"] = FileSecretStore(tmp_path / "secrets")
    rotate_credential(
        world["writer"], world["secrets"], PURPOSE, OLD, prove=lambda v: None, now=NOW
    )
    return world


def _rotate(env, value=NEW, *, prove=lambda v: None, recheck=None):
    return rotate_credential(
        env["writer"], env["secrets"], PURPOSE, value, prove=prove, recheck=recheck, now=NOW
    )


def _events(conn):
    return [
        (r[0], json.loads(r[1]))
        for r in conn.execute(
            "SELECT kind, payload FROM audit_events WHERE kind LIKE 'admin.credential%' ORDER BY chain_seq"
        )
    ]


def test_failed_candidate_leaves_working_credential_active(env):
    def refuse(value):
        raise ValueError("the provider rejected it")

    with pytest.raises(CredentialCheckFailed):
        _rotate(env, prove=refuse)
    assert active_credential(env["conn"], env["secrets"], PURPOSE) == OLD
    assert env["secrets"].versions(PURPOSE) == [1]  # the rejected candidate is gone


def test_successful_rotation_switches_atomically_and_retires_old(env):
    assert _rotate(env) == 2
    assert active_credential(env["conn"], env["secrets"], PURPOSE) == NEW
    assert env["secrets"].versions(PURPOSE) == [2]
    states = (
        env["conn"]
        .execute(
            f"SELECT version, state FROM key_slots WHERE purpose = '{PURPOSE}' ORDER BY version"
        )
        .fetchall()
    )
    assert states == [(1, "DESTROYED"), (2, "ACTIVE")]
    kinds = [k for k, _ in _events(env["conn"])]
    assert kinds == ["admin.credential_rotation", "admin.credential_rotation"]


def test_revoke_makes_adapter_not_configured(env):
    assert credential_status(env["conn"], PURPOSE) == "CONFIGURED"
    revoke_credential(env["writer"], env["secrets"], PURPOSE, now=NOW)
    assert credential_status(env["conn"], PURPOSE) == "NOT_CONFIGURED"
    assert active_credential(env["conn"], env["secrets"], PURPOSE) is None
    assert env["secrets"].versions(PURPOSE) == []
    assert _events(env["conn"])[-1] == (
        "admin.credential_revoked",
        {"purpose": PURPOSE, "version": 1},
    )


def test_post_activation_recheck_failure_rolls_back_to_the_intact_old_slot(env):
    def fail_recheck(value):
        raise ValueError("live check failed after activation")

    with pytest.raises(CredentialCheckFailed):
        _rotate(env, recheck=fail_recheck)
    assert active_credential(env["conn"], env["secrets"], PURPOSE) == OLD
    states = dict(
        env["conn"]
        .execute(f"SELECT version, state FROM key_slots WHERE purpose = '{PURPOSE}'")
        .fetchall()
    )
    assert states == {1: "ACTIVE", 2: "ORPHAN"}
    assert [k for k, _ in _events(env["conn"])][-2:] == [
        "admin.credential_rotation",
        "admin.credential_rotation_rolled_back",
    ]


def test_rotation_refused_while_degraded_and_for_non_credentials(env):
    latch_degraded(env["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    with pytest.raises(AuditIntegrityDegraded):
        _rotate(env)
    with pytest.raises(KeySlotError, match="not a provider credential"):
        rotate_credential(
            env["writer"], env["secrets"], "audit-chain-key", NEW, prove=lambda v: None, now=NOW
        )


def test_credential_values_never_in_events_logs_or_errors(env, caplog):
    caplog.set_level(logging.DEBUG)

    def refuse(value):
        raise ValueError("rejected")

    with pytest.raises(CredentialCheckFailed) as raised:
        _rotate(env, prove=refuse)
    _rotate(env)
    blob = " ".join(
        [str(raised.value), repr(raised.value), caplog.text]
        + [r[0] for r in env["conn"].execute("SELECT payload FROM audit_events")]
        + [r[0] for r in env["conn"].execute("SELECT key_id FROM key_slots")]
    )
    for value in (OLD, NEW):
        assert value.decode() not in blob and "canary" not in blob


def test_a_first_activation_that_fails_its_recheck_leaves_nothing_configured(tmp_path):
    world = comms_world(tmp_path)
    secrets = FileSecretStore(tmp_path / "secrets")

    def fail(value):
        raise ValueError("no")

    with pytest.raises(CredentialCheckFailed):
        rotate_credential(
            world["writer"], secrets, PURPOSE, NEW, prove=lambda v: None, recheck=fail, now=NOW
        )
    assert credential_status(world["conn"], PURPOSE) == "NOT_CONFIGURED"
    assert _events(world["conn"])[-1][1]["restored_version"] == 0
