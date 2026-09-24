"""The disclosure pipeline without consent (comms spec v0.2; 5b-3 design §1, §2.4)."""

import base64
import inspect

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.disclosure.coordinator import DISCLOSURE_STEPS, DisclosureCoordinator
from comms.transports.telegram.disclosure.keys import publish_verification_key
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.keys.store import key_id, load_key
from comms.transports.telegram.storage.settings import set_setting
from tests.coordinator_fixtures import build_coordinator


def _publish_disclosure_key(conn) -> None:
    raw = Ed25519PrivateKey.from_private_bytes(load_key("disclosure-key")).public_key()
    publish_verification_key(
        conn,
        key_id=key_id("disclosure-key"),
        purpose="disclosure_proof",
        algorithm="Ed25519",
        public_key_b64url=base64.urlsafe_b64encode(raw.public_bytes_raw()).rstrip(b"=").decode(),
        activated_at="2026-09-24T00:00:00Z",
    )


def test_the_coordinator_has_no_consent_seam():
    assert "consent" not in inspect.signature(DisclosureCoordinator).parameters
    assert not any(step.startswith("consent") for step in DISCLOSURE_STEPS)


async def test_a_disclosure_completes_owner_direct(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={"limit": 2}, adapter=adapter
    )
    assert outcome.released
    row = conn.execute(
        "SELECT proof_version, consent_key_id, consent_challenge_digest, soft_threshold_exceeded"
        " FROM disclosure_receipts WHERE disclosure_ref = ?",
        (outcome.disclosure_ref,),
    ).fetchone()
    assert tuple(row) == (2, None, None, 0)
    _publish_disclosure_key(conn)
    assert verify_persisted_receipt(conn, outcome.disclosure_ref)


async def test_soft_flag_equals_the_pre_reservation_consult(tmp_path):
    """Same decision point as the old prompt tier: `elevated` sets the flag."""
    coordinator, conn, adapter = build_coordinator(tmp_path)
    set_setting(conn, "exposure_budget.soft_records_per_client_global", 1)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={"limit": 2}, adapter=adapter
    )
    assert outcome.released
    flag = conn.execute(
        "SELECT soft_threshold_exceeded FROM disclosure_receipts WHERE disclosure_ref = ?",
        (outcome.disclosure_ref,),
    ).fetchone()[0]
    event = conn.execute(
        "SELECT error_code FROM audit_events WHERE disclosure_ref = ?", (outcome.disclosure_ref,)
    ).fetchone()[0]
    assert (flag, event) == (1, "soft_threshold_exceeded")
    _publish_disclosure_key(conn)
    assert verify_persisted_receipt(conn, outcome.disclosure_ref)


async def test_hard_refusal_still_precedes_retrieval(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)
    adapter.calls.clear()
    set_setting(conn, "exposure_budget.soft_records_per_client_global", 1)
    set_setting(conn, "exposure_budget.hard_records_per_client_global", 1)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={"limit": 2}, adapter=adapter
    )
    assert (outcome.released, outcome.error_code) == (False, "EXPOSURE_BUDGET_EXCEEDED")
    assert adapter.calls == []
    assert conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0] == 0
