"""The repair ceremony and its ordering (design §6.8), and receipt rebuild."""

import pytest

from comms.transports.telegram.disclosure.audit.anchor import (
    CLEAN,
    FAIL_CLOSED,
    RECOVERY_REQUIRED,
    AnchorError,
    derive_integrity,
    repair_anchor,
)
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.keys.store import key_id, load_key
from comms.transports.telegram.storage.settings import get_setting
from tests.authority_fixtures import drop_legacy_audit_guards
from tests.coordinator_fixtures import build_coordinator


async def _degraded(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, crash_at="refresh_anchor")
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    return conn, load_key("audit-chain-key"), tmp_path / "anchor" / "anchor.json"


async def test_a_crashed_anchor_leaves_the_chain_one_ahead(tmp_path):
    conn, chain_key, path = await _degraded(tmp_path)
    # No anchor was ever written, so there is nothing to compare against and
    # the state fails closed rather than guessing.
    assert derive_integrity(conn, chain_key, path) == FAIL_CLOSED


async def test_repair_restores_clean_and_audits_itself(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)
    # One clean disclosure so an anchor exists, then a failed one.
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    path = tmp_path / "anchor" / "anchor.json"
    chain_key = load_key("audit-chain-key")
    assert derive_integrity(conn, chain_key, path) == CLEAN

    coordinator._crash_at = "refresh_anchor"
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    assert derive_integrity(conn, chain_key, path) == RECOVERY_REQUIRED
    assert get_setting(conn, "audit.integrity_degraded") == 1

    repair_anchor(
        conn, chain_key, load_key("audit-checkpoint-key"), path, now="2026-09-22T01:00:00Z"
    )

    assert derive_integrity(conn, chain_key, path) == CLEAN
    # The repair is itself a normally chained, anchored event, and the latch
    # clears only after that event is anchored.
    last = conn.execute(
        "SELECT tool_name, disclosure_ref FROM audit_events ORDER BY chain_seq DESC LIMIT 1"
    ).fetchone()
    assert last[0] == "admin.repair_anchor"
    assert last[1].startswith("tdr_"), "the repair names the disclosure that was withheld"
    assert get_setting(conn, "audit.integrity_degraded") == 0
    assert get_setting(conn, "audit.degraded_disclosure_ref") == ""


async def test_repair_refuses_when_the_chain_does_not_extend(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    path = tmp_path / "anchor" / "anchor.json"
    chain_key = load_key("audit-chain-key")

    # Anchor ahead of the head: the database lost committed history. Moving
    # the pointer would launder the evidence, so repair must refuse.
    drop_legacy_audit_guards(conn)  # an attacker with raw DB access
    conn.execute("DELETE FROM audit_events")
    conn.commit()
    assert derive_integrity(conn, chain_key, path) == FAIL_CLOSED

    with pytest.raises(AnchorError):
        repair_anchor(
            conn, chain_key, load_key("audit-checkpoint-key"), path, now="2026-09-22T01:00:00Z"
        )


async def test_a_persisted_receipt_rebuilds_and_verifies(tmp_path):
    from comms.transports.telegram.disclosure.keys import publish_verification_key

    coordinator, conn, adapter = build_coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    public = outcome.meta["disclosure"]["proof_payload"]
    assert public  # payload present

    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.from_private_bytes(load_key("disclosure-key")).public_key()
    b64 = base64.urlsafe_b64encode(raw.public_bytes_raw()).rstrip(b"=").decode("ascii")
    publish_verification_key(
        conn,
        key_id=key_id("disclosure-key"),
        purpose="disclosure_proof",
        algorithm="Ed25519",
        public_key_b64url=b64,
        activated_at="2026-09-22T00:00:00Z",
    )

    assert verify_persisted_receipt(conn, outcome.disclosure_ref)


async def test_a_rotated_client_credential_does_not_break_an_old_receipt(tmp_path):
    from comms.transports.telegram.disclosure.keys import publish_verification_key

    coordinator, conn, adapter = build_coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.from_private_bytes(load_key("disclosure-key")).public_key()
    b64 = base64.urlsafe_b64encode(raw.public_bytes_raw()).rstrip(b"=").decode("ascii")
    publish_verification_key(
        conn,
        key_id=key_id("disclosure-key"),
        purpose="disclosure_proof",
        algorithm="Ed25519",
        public_key_b64url=b64,
        activated_at="2026-09-22T00:00:00Z",
    )

    # client rotate changes credentials and auth_binding. It must NOT re-mint
    # client_ref: the payload is rebuilt from that ref, so re-minting it would
    # silently invalidate every retained receipt naming this client.
    before = conn.execute("SELECT client_ref FROM mcp_clients WHERE id = 1").fetchone()[0]
    conn.execute("UPDATE mcp_clients SET auth_binding = 'rotated', rotated_at = 'now' WHERE id = 1")
    conn.commit()
    after = conn.execute("SELECT client_ref FROM mcp_clients WHERE id = 1").fetchone()[0]

    assert before == after
    assert verify_persisted_receipt(conn, outcome.disclosure_ref)
