"""D39-PRE Task E7: the keys, audit and cutover operator commands, over a daemon-shaped world."""

import pytest

from comms.core.audit.integrity import is_degraded, latch_degraded
from comms.runtime.operator.cutover import CUTOVER_PENDING
from tests.runtime.operator.conftest import _now


def test_a_fresh_install_cuts_over_verifies_and_releases_the_write_hold(daemon_world):
    run, conn = daemon_world["run"], daemon_world["state"].conn
    assert run("cutover", "status") == {
        "phase": "NONE",
        "cutover": None,
        "writes_held": False,
    }
    latch_degraded(conn, reason=CUTOVER_PENDING, now=_now())  # what the daemon does at start
    assert run("cutover", "status")["writes_held"] is True
    done = run("cutover", "run")
    assert done == {"phase": "COMPLETE", "writes_released": True}
    assert not is_degraded(conn)
    report = run("audit", "verify", all=True)
    assert report == {"ok": True, "legacy": "ok", "lineage": "ok", "comms": "ok", "problems": []}
    assert run("audit", "verify") == {"comms": "CLEAN", "ok": True}
    assert run("cutover", "run")["phase"] == "COMPLETE"  # resumable and idempotent


def test_the_cutover_never_clears_an_integrity_latch(daemon_world):
    run, conn = daemon_world["run"], daemon_world["state"].conn
    latch_degraded(conn, reason="ANCHOR_REFRESH_FAILED", now=_now())
    assert run("cutover", "run") == {"phase": "COMPLETE", "writes_released": False}
    assert is_degraded(conn)


def test_keys_list_carries_metadata_only(daemon_world):
    listed = daemon_world["run"]("keys", "list")["keys"]
    purposes = {k["purpose"] for k in listed}
    assert {"audit-chain-key", "cursor-key", "backup-key"} <= purposes
    assert all(set(k) == {"purpose", "version", "key_id", "state"} for k in listed)


def test_keys_rotate_bumps_the_version_audited(daemon_world):
    run = daemon_world["run"]
    run("cutover", "run")
    before = {k["purpose"]: k for k in run("keys", "list")["keys"] if k["state"] == "ACTIVE"}
    rotated = run("keys", "rotate", purpose="cursor-key")
    assert rotated["version"] == before["cursor-key"]["version"] + 1
    assert rotated["key_id"] != before["cursor-key"]["key_id"]
    assert run("audit", "verify", all=True)["ok"] is True


def test_the_database_key_and_credentials_are_not_rotated_here(daemon_world):
    run = daemon_world["run"]
    with pytest.raises(ValueError, match="daemon stopped"):
        run("keys", "rotate", purpose="comms-db-key")
    with pytest.raises(ValueError, match="own rotation protocol|unknown key purpose"):
        run("keys", "rotate", purpose="telegram-bot-token")


def test_mark_signer_moves_only_toward_less_trust(daemon_world):
    run = daemon_world["run"]
    run("cutover", "run")
    signer = next(k for k in run("keys", "list")["keys"] if k["purpose"] == "backup-key")
    assert (
        run("keys", "mark-signer", key_id=signer["key_id"], state="VERIFICATION_ONLY")["state"]
        == "VERIFICATION_ONLY"
    )
    with pytest.raises(ValueError, match="one-way"):
        run("keys", "mark-signer", key_id=signer["key_id"], state="ACTIVE")


def test_audit_repair_refuses_a_healthy_or_empty_chain(daemon_world):
    run = daemon_world["run"]
    with pytest.raises(ValueError, match="repair refused: (CHAIN_EMPTY|ANCHOR_MISSING)"):
        run("audit", "repair")
    run("cutover", "run")
    repaired = run("audit", "repair")  # a healthy anchor names the head: repair re-anchors it
    assert repaired["repaired"] is True and repaired["to"] >= repaired["from"]
    assert run("audit", "verify", all=True)["ok"] is True


def test_outside_the_daemon_the_legacy_commands_refuse(daemon_world):
    from comms.runtime.operator import OperatorContext, operator_handler

    ctx = daemon_world["ctx"]
    bare = operator_handler(OperatorContext(writer=ctx.writer, store=ctx.store, clock=ctx.clock))
    with pytest.raises(ValueError, match="legacy chain is not attached"):
        bare({"command": ["cutover", "run"]})
    with pytest.raises(ValueError, match="unknown operator command"):
        bare({"command": ["keys", "provision"]})  # local, never over the socket
