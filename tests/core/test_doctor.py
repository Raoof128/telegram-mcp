"""comms v0.3 Task B29: comms doctor — one planted problem per check, and nothing secret in the output."""

import os
from datetime import timedelta

import pytest

from comms.core.audit import cutover
from comms.core.audit.chain import COMMS, insert_checkpoint
from comms.core.audit.integrity import latch_degraded
from comms.core.credentials import rotate_credential
from comms.core.doctor import doctor
from comms.core.keys import rotate as rot
from comms.core.keys.secrets import FileSecretStore
from comms.core.keys.slots import load_active
from comms.core.maintenance.retention import RetentionPolicy, run_retention
from comms.core.storage.db import write_tx
from comms.transports.telegram.runtime.cutover_barrier import legacy_verifier
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.core.audit.legacy_fixtures import CHAIN_KEY, comms_world, public_for
from tests.core.campaign_helpers import NOW, person, ready

KEEP = RetentionPolicy(3650, 3650, 3650, 3650, 3650, 3650)
TOKEN = b"123456:bot-token-canary"


@pytest.fixture
def w(tmp_path):
    world = comms_world(tmp_path, bearer=True)
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    for purpose in ("campaign-commit-key", "backup-key"):  # provisioned by `comms keys` (Part D)
        rot.rotate(
            world["writer"],
            world["store"],
            purpose,
            material=os.urandom(32),
            prove=lambda m: None,
            now=NOW,
        )
    world["secrets"] = FileSecretStore(tmp_path / "secrets")
    rotate_credential(
        world["writer"],
        world["secrets"],
        "telegram-bot-token",
        TOKEN,
        prove=lambda v: None,
        now=NOW,
    )
    legacy = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    run_retention(world["conn"], legacy, KEEP, world["writer"], now=NOW, store=world["store"])
    return world


def _codes(w, *, now=NOW):
    findings = doctor(
        w["conn"],
        w["store"],
        now=now,
        legacy_conn=w["port"].conn,
        legacy=legacy_verifier(CHAIN_KEY, public_for),
    )
    return {f.code for f in findings}, findings


def test_a_healthy_installation_reports_only_unconfigured_credentials(w):
    codes, _ = _codes(w)
    assert codes <= {"CREDENTIAL_NOT_CONFIGURED"}


def test_missing_key(w):
    w["conn"].execute(
        "UPDATE key_slots SET state = 'RETIRED' WHERE purpose = 'audit-checkpoint-key' AND state = 'ACTIVE'"
    )
    w["conn"].commit()
    assert "KEY_MISSING" in _codes(w)[0]


def test_invalid_key_material(w):
    path = next((w["tmp"] / "slots" / "audit-chain-key").iterdir())
    path.write_bytes(os.urandom(32))
    assert "KEY_INVALID" in _codes(w)[0]


def test_orphan_slot(w):
    w["store"].write_version("audit-checkpoint-key", os.urandom(32))
    assert "ORPHAN_KEY_SLOT" in _codes(w)[0]


def test_a_client_seed_is_registered_by_its_client_row_not_an_orphan(w):
    """D39-PRE E9: cml1 client seeds are registered in ``clients`` (D27), not ``key_slots``."""
    from comms.core.auth import clients

    (w["tmp"] / "helper").mkdir(mode=0o700)
    clients.add_client(
        w["conn"], w["store"], "c", now=NOW, helper_path=w["tmp"] / "helper" / "seed"
    )
    assert "ORPHAN_KEY_SLOT" not in _codes(w)[0]
    w["store"].write_version("cml1-client-seed", os.urandom(32))  # a seed no client names
    assert "ORPHAN_KEY_SLOT" in _codes(w)[0]


def test_key_coverage_gap(w):
    key = load_active(w["conn"], w["store"], "audit-checkpoint-key")[0]
    with write_tx(w["conn"]):
        insert_checkpoint(
            w["conn"], COMMS, key, now="2026-09-24T00:00:00.000000Z", reason="PERIODIC"
        )
    w["conn"].execute("DELETE FROM verification_keys WHERE purpose = 'audit-checkpoint-key'")
    w["conn"].commit()
    assert "KEY_COVERAGE_GAP" in _codes(w)[0]


def test_maintenance_overdue(w):
    assert "MAINTENANCE_OVERDUE" in _codes(w, now=NOW + timedelta(days=30))[0]


def test_comms_chain_invalid(w):
    w["conn"].execute("DROP TRIGGER audit_events_append_only_u")
    w["conn"].execute("UPDATE audit_events SET payload = '{}' WHERE chain_seq = 1")
    w["conn"].commit()
    assert "COMMS_CHAIN_INVALID" in _codes(w)[0]


def test_legacy_chain_invalid(w):
    w["port"].conn.execute("DROP TRIGGER IF EXISTS legacy_audit_sealed")
    w["port"].conn.execute("UPDATE audit_events SET status = 'error' WHERE chain_seq = 1")
    w["port"].conn.commit()
    assert "LEGACY_CHAIN_INVALID" in _codes(w)[0]


def test_lineage_invalid(w):
    w["conn"].execute("DROP TRIGGER audit_lineage_immutable_u")
    w["conn"].execute("UPDATE audit_lineage SET legacy_final_epoch = 9")
    w["conn"].commit()
    assert "LINEAGE_INVALID" in _codes(w)[0]


def test_credentials_are_reported_by_presence_only(w):
    codes, findings = _codes(w)
    assert "CREDENTIAL_NOT_CONFIGURED" in codes
    configured = {f.subject for f in findings if f.code == "CREDENTIAL_NOT_CONFIGURED"}
    assert "telegram-bot-token" not in configured and "meta-access-token" in configured


def test_latch(w):
    latch_degraded(w["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    assert "AUDIT_DEGRADED" in _codes(w)[0]


def test_cutover_phase(tmp_path):
    world = comms_world(tmp_path)
    codes = {f.code for f in doctor(world["conn"], world["store"], now=NOW)}
    assert "CUTOVER_INCOMPLETE" in codes


def test_doctor_output_holds_no_secret_identity_or_body(w):
    rcp, _ = person(w["conn"], phone="+61400000077")
    ready(w["conn"], {"recipients": [rcp]}, body="doctor body canary")
    w["store"].write_version("audit-checkpoint-key", os.urandom(32))  # something to report
    latch_degraded(w["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    _, findings = _codes(w, now=NOW + timedelta(days=30))
    text = repr(findings)
    material = [
        w["store"].read(p, v)
        for p in ("audit-chain-key", "audit-checkpoint-key")
        for v in w["store"].versions(p)
    ]
    assert "+61400000077" not in text and "canary" not in text
    assert all(m.hex() not in text for m in material)
