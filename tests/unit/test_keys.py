"""Task 2: key registry, file store, and daemon-side pairing tests.

TDD RED step: these tests are written before ``telegram_mcp.keys`` exists.
Controller ruling: host-mutating actions are forbidden — permission tests use
chmod'd fixtures inside ``tmp_path`` only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import stat

import pytest


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """A 0700 tmp store dir, bound as the process-wide key store."""
    os.chmod(tmp_path, 0o700)
    from telegram_mcp.keys import store

    monkeypatch.setattr(store, "_STORE_DIR", None)
    store.set_store_dir(tmp_path)
    return tmp_path


# --- Registry (brief Step 1, verbatim) ---------------------------------------


def test_registry_has_thirteen_purposes_phase_split():
    from telegram_mcp.keys.registry import KEY_REGISTRY

    assert len(KEY_REGISTRY) == 12 + 1  # 12 spec purposes + agent-transport-key (origin impl)
    assert sum(1 for s in KEY_REGISTRY.values() if s.origin == "impl") == 1
    phase2 = {n for n, s in KEY_REGISTRY.items() if s.required_phase == 2}
    assert "disclosure-key" not in phase2
    assert "challenge-key" in phase2
    assert "agent-transport-key" in phase2


def test_registry_rows_match_design_section_3():
    from telegram_mcp.keys.registry import KEY_REGISTRY

    assert KEY_REGISTRY["principal-key"].algorithm == "HMAC-SHA-256"
    assert KEY_REGISTRY["cursor-key"].algorithm == "HMAC-SHA-256"
    assert KEY_REGISTRY["privacy-key"].algorithm == "HMAC-SHA-256"
    assert KEY_REGISTRY["challenge-key"].algorithm == "Ed25519"
    assert KEY_REGISTRY["lease-seed"].algorithm == "HMAC-SHA-256"
    assert KEY_REGISTRY["tunnel-tls-key"].algorithm == "X.509/SPKI"
    assert KEY_REGISTRY["tunnel-mtls-key"].algorithm == "X.509/SPKI"
    assert KEY_REGISTRY["agent-approval-key"].algorithm == "P-256 Secure Enclave"
    for name in (
        "principal-key",
        "cursor-key",
        "privacy-key",
        "challenge-key",
        "lease-seed",
    ):
        spec = KEY_REGISTRY[name]
        assert spec.owner == "runtime account"
        assert spec.persistent is True
        assert spec.required_phase == 2
        assert spec.origin == "spec"
    # Phase-3 rows exist as registry-only placeholders: never provisioned early.
    for name in ("disclosure-key", "audit-checkpoint-key", "audit-chain-key", "backup-key"):
        spec = KEY_REGISTRY[name]
        assert spec.required_phase == 3
        assert spec.origin == "spec"
        assert spec.persistent is False
    # The single implementation row beyond the twelve spec purposes.
    impl = KEY_REGISTRY["agent-transport-key"]
    assert impl.origin == "impl"
    assert impl.required_phase == 2
    assert impl.persistent is True


def test_phase3_rows_pin_algorithms_per_spec_9_6_1():
    from telegram_mcp.keys.registry import KEY_REGISTRY

    assert KEY_REGISTRY["disclosure-key"].algorithm == "Ed25519"
    assert KEY_REGISTRY["audit-checkpoint-key"].algorithm == "Ed25519"
    assert KEY_REGISTRY["audit-chain-key"].algorithm == "HMAC-SHA-256"
    # Controller ruling (spec §9.6.1): backup-key is the Ed25519
    # policy-backup signing key, a distinct purpose — not HMAC.
    assert KEY_REGISTRY["backup-key"].algorithm == "Ed25519"


def test_keyspec_is_frozen():
    from telegram_mcp.keys.registry import KeySpec

    spec = KeySpec(
        algorithm="Ed25519",
        owner="runtime account",
        persistent=True,
        required_phase=2,
        origin="spec",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.owner = " Mallory "  # type: ignore[misc]


def test_keyspec_rejects_bad_origin_and_phase():
    from telegram_mcp.keys.registry import KeySpec

    with pytest.raises(ValueError):
        KeySpec(algorithm="Ed25519", owner="x", persistent=True, required_phase=2, origin="lore")
    with pytest.raises(ValueError):
        KeySpec(algorithm="Ed25519", owner="x", persistent=True, required_phase=4, origin="spec")


# --- Store (brief Step 3) -----------------------------------------------------


def test_no_phase3_private_keys_provisioned(tmp_path):
    from telegram_mcp.keys.store import provision_missing

    created = provision_missing(tmp_path, phases=(2,))
    assert not any("disclosure" in name or "audit" in name or "backup" in name for name in created)


def test_provision_creates_only_phase2_file_backed_rows(store_dir):
    from telegram_mcp.keys.store import provision_missing

    created = provision_missing(store_dir, phases=(2,))
    assert set(created) == {"principal-key", "cursor-key", "privacy-key", "challenge-key"}
    for name in created:
        mode = stat.S_IMODE(os.stat(store_dir / name).st_mode)
        assert mode == 0o600


def test_provision_phases_3_creates_exactly_the_three_phase_three_rows(store_dir):
    from telegram_mcp.keys.store import provision_missing

    assert sorted(provision_missing(store_dir, phases=(3,))) == [
        "audit-chain-key",
        "audit-checkpoint-key",
        "disclosure-key",
    ]


def test_provision_never_overwrites(store_dir):
    from telegram_mcp.keys.store import load_key, provision_missing

    first = {name: load_key(name) for name in provision_missing(store_dir, phases=(2,))}
    assert provision_missing(store_dir, phases=(2,)) == []
    for name, material in first.items():
        assert load_key(name) == material


def test_load_enforces_0600(store_dir):
    from telegram_mcp.keys.store import KeyStoreError, load_key, provision_missing

    provision_missing(store_dir, phases=(2,))
    target = store_dir / "challenge-key"
    os.chmod(target, 0o644)
    with pytest.raises(KeyStoreError, match="permissions must be 0600"):
        load_key("challenge-key")


def test_provision_rejects_bad_parent_dir(tmp_path):
    from telegram_mcp.keys.store import KeyStoreError, provision_missing

    os.chmod(tmp_path, 0o755)
    with pytest.raises(KeyStoreError, match="0700"):
        provision_missing(tmp_path, phases=(2,))


def test_load_unknown_purpose_raises(store_dir):
    from telegram_mcp.keys.store import KeyStoreError, load_key

    with pytest.raises(KeyStoreError, match="unknown key purpose"):
        load_key("mallory-key")


def test_load_missing_file_raises(store_dir):
    from telegram_mcp.keys.store import KeyStoreError, load_key

    # Phase-3 rows are registry-only: no private file may exist yet.
    with pytest.raises(KeyStoreError, match="key file is missing"):
        load_key("disclosure-key")


def test_key_id_ed25519_matches_raw_public_bytes(store_dir):
    import hashlib as _hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.keys.store import key_id, load_key, provision_missing

    provision_missing(store_dir, phases=(2,))
    seed = load_key("challenge-key")
    assert len(seed) == 32
    raw_public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    assert key_id("challenge-key") == "ed25519:sha256:" + _hashlib.sha256(raw_public).hexdigest()


def test_key_id_hmac_shape(store_dir):
    from telegram_mcp.keys.store import key_id, load_key, provision_missing

    provision_missing(store_dir, phases=(2,))
    for name in ("principal-key", "cursor-key", "privacy-key"):
        assert key_id(name) == "hmac:sha256:" + hashlib.sha256(load_key(name)).hexdigest()


def _self_signed_cert():
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


def test_tunnel_pin_fingerprint_is_spki(store_dir):
    from cryptography.hazmat.primitives import serialization

    from telegram_mcp.keys import pairing
    from telegram_mcp.keys.store import key_id

    cert = _self_signed_cert()
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    shown = pairing.import_peer_pin("tunnel-tls-key", cert.public_bytes(serialization.Encoding.DER))
    assert shown == "spki:sha256:" + hashlib.sha256(der).hexdigest()
    assert key_id("tunnel-tls-key") == shown


def test_lease_seed_per_client(store_dir):
    from telegram_mcp.keys.store import load_key, provision_lease_seed

    seed_a = provision_lease_seed(store_dir, "tcl_" + "d" * 26)
    seed_b = provision_lease_seed(store_dir, "tcl_" + "e" * 26)
    assert len(seed_a) == 32 and seed_a != seed_b
    assert provision_lease_seed(store_dir, "tcl_" + "d" * 26) == seed_a
    mode = stat.S_IMODE(os.stat(store_dir / ("lease-seed." + "tcl_" + "d" * 26)).st_mode)
    assert mode == 0o600
    # The lease-seed registry row is a template: no single file-backed key.
    with pytest.raises(Exception, match="key file is missing"):
        load_key("lease-seed")


def test_lease_seed_rejects_unsafe_client_ref(store_dir):
    from telegram_mcp.keys.store import KeyStoreError, provision_lease_seed

    for bad in ("", "../escape", "a/b", "x" * 129):
        with pytest.raises(KeyStoreError, match="invalid client reference"):
            provision_lease_seed(store_dir, bad)


# --- Pairing (brief Step 5) ---------------------------------------------------


def test_pairing_agent_two_slots(store_dir):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.keys import pairing

    approval_pub = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    transport_pub = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    approval_fp = pairing.import_peer_pin("agent-approval-key", approval_pub)
    transport_fp = pairing.import_peer_pin("agent-transport-key", transport_pub)
    assert approval_fp == "p256:sha256:" + hashlib.sha256(approval_pub).hexdigest()
    assert transport_fp == "ed25519:sha256:" + hashlib.sha256(transport_pub).hexdigest()
    assert approval_fp != transport_fp
    assert pairing.export_public("agent-approval-key") == approval_pub
    assert pairing.export_public("agent-transport-key") == transport_pub
    assert pairing.verify_fingerprint("agent-approval-key", approval_fp) is True
    assert pairing.verify_fingerprint("agent-approval-key", transport_fp) is False


def test_pairing_challenge_export_and_verify(store_dir):
    from telegram_mcp.keys import pairing
    from telegram_mcp.keys.store import key_id, provision_missing

    provision_missing(store_dir, phases=(2,))
    raw = pairing.export_public("challenge-key")
    assert len(raw) == 32  # raw Ed25519 public bytes, not the seed
    assert pairing.verify_fingerprint("challenge-key", key_id("challenge-key")) is True
    assert pairing.verify_fingerprint("challenge-key", "ed25519:sha256:" + "0" * 64) is False


def test_pairing_rejects_hmac_pin(store_dir):
    from telegram_mcp.keys import pairing
    from telegram_mcp.keys.store import KeyStoreError

    with pytest.raises(KeyStoreError, match="no public half"):
        pairing.import_peer_pin("cursor-key", b"\x00" * 32)


def test_pairing_unknown_purpose_raises(store_dir):
    from telegram_mcp.keys import pairing
    from telegram_mcp.keys.store import KeyStoreError

    with pytest.raises(KeyStoreError, match="unknown key purpose"):
        pairing.import_peer_pin("mallory-key", b"\x00" * 32)
    assert pairing.verify_fingerprint("mallory-key", "ed25519:sha256:" + "0" * 64) is False


def test_pairing_rejects_malformed_public(store_dir):
    from telegram_mcp.keys import pairing
    from telegram_mcp.keys.store import KeyStoreError

    with pytest.raises(KeyStoreError, match="invalid key material"):
        pairing.import_peer_pin("agent-transport-key", b"too-short")
    with pytest.raises(KeyStoreError, match="invalid key material"):
        pairing.import_peer_pin("agent-approval-key", b"\x00" * 32)


def test_phase_three_rows_are_provisioned_and_have_distinct_ids(tmp_path):
    from telegram_mcp.keys.store import key_id, provision_missing

    created = provision_missing(tmp_path, phases=(2, 3))

    assert {"disclosure-key", "audit-checkpoint-key", "audit-chain-key"} <= set(created)
    assert (tmp_path / "disclosure-key").stat().st_mode & 0o777 == 0o600
    assert key_id("disclosure-key").startswith("ed25519:sha256:")
    assert key_id("audit-chain-key").startswith("hmac:sha256:")
    # Purpose separation: three rows, three distinct recomputed ids.
    ids = {key_id(n) for n in ("disclosure-key", "audit-checkpoint-key", "audit-chain-key")}
    assert len(ids) == 3


def test_phase_two_only_provisioning_still_skips_phase_three(tmp_path):
    from telegram_mcp.keys.store import provision_missing

    created = provision_missing(tmp_path, phases=(2,))

    assert "disclosure-key" not in created
    assert not (tmp_path / "disclosure-key").exists()
