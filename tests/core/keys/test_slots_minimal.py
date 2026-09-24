"""comms v0.3 Task A1b: versioned immutable key slots, verification keys, comms audit key bootstrap."""

import os
import stat

import pytest
import sqlcipher3

from comms.core.keys import ids
from comms.core.keys.slots import (
    KeySlotError,
    KeySlotStore,
    bootstrap_comms_audit_keys,
    load_active,
)
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_slot_files_are_immutable_0600_in_a_0700_dir(tmp_path):
    store = KeySlotStore(tmp_path / "slots")
    assert store.write_version("cursor-key", b"k" * 32) == 1
    assert store.write_version("cursor-key", b"j" * 32) == 2
    assert store.versions("cursor-key") == [1, 2]
    assert _mode(tmp_path / "slots") == 0o700
    assert _mode(tmp_path / "slots" / "cursor-key" / "1") == 0o600
    assert store.read("cursor-key", 1) == b"k" * 32
    store.destroy("cursor-key", 1)
    assert store.versions("cursor-key") == [2]
    with pytest.raises(KeySlotError):
        store.read("cursor-key", 1)


def test_a_group_readable_slot_dir_is_refused(tmp_path):
    d = tmp_path / "slots"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    with pytest.raises(KeySlotError, match="permissions"):
        KeySlotStore(d)


def test_bootstrap_is_idempotent_and_registers_one_active_version_each(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    rows = conn.execute("SELECT purpose, version, state FROM key_slots ORDER BY purpose").fetchall()
    assert rows == [("audit-chain-key", 1, "ACTIVE"), ("audit-checkpoint-key", 1, "ACTIVE")]
    assert store.versions("audit-chain-key") == [1]
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
        conn.execute(
            "INSERT INTO key_slots VALUES ('audit-chain-key', 2, 'x', 'ACTIVE', ?, NULL)", (fx.T0,)
        )


def test_checkpoint_public_key_in_verification_keys_private_never_in_db(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    seed = store.read("audit-checkpoint-key", 1)
    [(key_id, algorithm, public, trust)] = conn.execute(
        "SELECT key_id, algorithm, public_key, trust_state FROM verification_keys"
    ).fetchall()
    assert (algorithm, trust) == ("ed25519", "ACTIVE")
    assert key_id == ids.ed25519_key_id(bytes(public))
    assert seed not in bytes(public)
    dump = repr(conn.execute("SELECT * FROM key_slots").fetchall())
    assert seed.hex() not in dump and store.read("audit-chain-key", 1).hex() not in dump


def test_load_active_recomputes_and_checks_the_key_id(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    material, key_id = load_active(conn, store, "audit-chain-key")
    assert key_id == ids.hmac_key_id(material)
    conn.execute("DROP TRIGGER IF EXISTS key_slots_binding_immutable")
    conn.execute(
        "UPDATE key_slots SET key_id = 'hmac:sha256:' || printf('%064d', 0) WHERE purpose = 'audit-chain-key'"
    )
    with pytest.raises(KeySlotError, match="key id mismatch"):
        load_active(conn, store, "audit-chain-key")


def test_verification_keys_public_half_is_immutable(tmp_path):
    conn = fx.migrated(tmp_path)
    bootstrap_comms_audit_keys(conn, KeySlotStore(tmp_path / "slots"), now=NOW)
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError, match="immutable"):
        conn.execute("UPDATE verification_keys SET public_key = x'00'")
    conn.execute("UPDATE verification_keys SET trust_state = 'TRUSTED_RETIRED'")


def test_legacy_store_uses_the_core_key_id_rule():
    import inspect

    from comms.transports.telegram.keys import store as legacy

    src = inspect.getsource(legacy)
    assert "from comms.core.keys import ids" in src
    assert '"hmac:sha256:" + hashlib' not in src and '"ed25519:sha256:" + hashlib' not in src
