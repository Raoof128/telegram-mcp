"""comms v0.3 Task B4: the key-purpose table and its destruction rules (design §B.4; A9–A11, A38, G8)."""

import os

import pytest

from comms.core.keys.purposes import PURPOSES, KeyPurpose
from comms.core.keys.slots import (
    KeySlotError,
    KeySlotStore,
    bootstrap_comms_audit_keys,
    load_active,
)
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

# design §B.4, row by row: (kind, rotation, public_registry, private_destroy)
DESIGN = {
    "audit-chain-key": ("hmac", "seal_epoch", False, "after_epoch_truncated"),
    "audit-checkpoint-key": ("ed25519", "new_id", True, "at_rotation"),
    "campaign-commit-key": ("hmac", "new_id", False, "while_commitments_retained"),
    "backup-key": ("ed25519", "new_id", True, "at_rotation"),
    "cursor-key": ("hmac", "invalidate", False, "at_once"),
    "comms-db-key": ("raw256", "rekey", False, "after_verified_reopen"),
    "cml1-client-seed": ("hmac", "new_id", False, "at_rotation"),
    "oauth-signing-key": ("ed25519", "new_id", True, "at_rotation"),
    "oauth-refresh-key": ("hmac", "new_id", False, "at_rotation"),
    "oauth-registration-key": ("hmac", "new_id", False, "at_rotation"),
    "telegram-session": ("opaque", "staged", False, "at_rotation"),
    "telegram-bot-token": ("opaque", "staged", False, "at_rotation"),
    "meta-access-token": ("opaque", "staged", False, "at_rotation"),
    "meta-app-secret": ("opaque", "staged", False, "at_rotation"),
    "meta-webhook-secret": ("opaque", "staged", False, "at_rotation"),
    "tls-key": ("opaque", "new_id", False, "at_rotation"),
    "principal-key": ("hmac", "refused", False, "while_dependency_proven"),
    "privacy-key": ("hmac", "refused", False, "while_dependency_proven"),
    "disclosure-key": ("ed25519", "refused", True, "owner_runbook"),
    "consent-approval-key": ("p256", "refused", True, "owner_runbook"),
    "consent-transport-key": ("p256", "refused", True, "owner_runbook"),
}


def test_purposes_match_the_design_table():
    assert set(PURPOSES) == set(DESIGN)
    for name, (kind, rotation, public, destroy) in DESIGN.items():
        purpose = PURPOSES[name]
        assert isinstance(purpose, KeyPurpose) and purpose.name == name
        assert (
            purpose.kind,
            purpose.rotation,
            purpose.public_registry,
            purpose.private_destroy,
        ) == (
            kind,
            rotation,
            public,
            destroy,
        ), name


def test_every_signer_has_a_public_registry_row(tmp_path):
    for purpose in PURPOSES.values():
        assert purpose.public_registry is (purpose.kind in {"ed25519", "p256"}), purpose.name
    conn = fx.migrated(tmp_path)
    bootstrap_comms_audit_keys(conn, KeySlotStore(tmp_path / "slots"), now=NOW)
    registered = {row[0] for row in conn.execute("SELECT purpose FROM verification_keys")}
    active_signers = {
        row[0]
        for row in conn.execute("SELECT purpose FROM key_slots WHERE state = 'ACTIVE'")
        if PURPOSES[row[0]].public_registry
    }
    assert active_signers and active_signers <= registered


def test_no_purpose_keeps_a_private_key_forever_without_a_rule():
    rules = {
        "at_rotation",
        "after_epoch_truncated",
        "while_commitments_retained",
        "at_once",
        "after_verified_reopen",
        "while_dependency_proven",
        "owner_runbook",
    }
    for purpose in PURPOSES.values():
        assert purpose.private_destroy in rules, purpose.name
    assert "never" not in {p.private_destroy for p in PURPOSES.values()}


def test_the_slot_store_accepts_only_known_purposes_and_never_mints_a_refused_one(tmp_path):
    store = KeySlotStore(tmp_path / "slots")
    with pytest.raises(KeySlotError, match="unknown key purpose"):
        store.write_version("made-up-key", os.urandom(32))
    for name in ("principal-key", "privacy-key", "disclosure-key", "consent-approval-key"):
        with pytest.raises(KeySlotError, match="retired"):
            store.write_version(name, os.urandom(32))


def test_key_material_never_in_repr_or_errors(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    material, _key_id = load_active(conn, store, "audit-chain-key")
    path = next((tmp_path / "slots" / "audit-chain-key").iterdir())
    path.chmod(0o644)
    with pytest.raises(KeySlotError) as refused:
        load_active(conn, store, "audit-chain-key")
    path.chmod(0o600)
    path.write_bytes(os.urandom(32))  # the stored key id no longer matches the material
    with pytest.raises(KeySlotError) as mismatched:
        load_active(conn, store, "audit-chain-key")
    for text in (
        repr(store),
        str(refused.value),
        str(mismatched.value),
        repr(PURPOSES["audit-chain-key"]),
    ):
        assert material.hex() not in text and path.read_bytes().hex() not in text
