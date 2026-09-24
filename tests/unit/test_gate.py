"""DisclosureGate seam and audit seam (Task 4, Phase-2a authority plan)."""

import dataclasses

import pytest

from comms.transports.telegram import audit_seam
from comms.transports.telegram.audit_seam import AuditEvent, emit_audit, get_audit_sink
from comms.transports.telegram.consent.broker import ConsumedChallenge
from comms.transports.telegram.consent.gate import (
    DisclosureDecision,
    DisclosureGate,
    SyntheticDisclosureGate,
)

# --- Step 1: failing seam test (verbatim from the brief) ---


async def test_synthetic_gate_accounts_nothing():
    gate = SyntheticDisclosureGate()
    decision = await gate.authorize_disclosure(challenge=None, snapshot=None)
    assert isinstance(decision, DisclosureDecision) and decision.allowed is True
    assert gate.accounted_records == 0


# --- Gate consumes a real ConsumedChallenge; still zero accounting ---


def _consumed() -> ConsumedChallenge:
    return ConsumedChallenge(
        handle="tgu_" + "a" * 26,
        tool="telegram_list_chats",
        principal="prn_" + "a" * 26,
        client="tcl_" + "b" * 26,
        account="tga_" + "c" * 26,
        policy_epoch=1,
        security_epoch=1,
        challenge_sha256="0" * 64,
        key_id="p256:sha256:" + "f" * 64,
        nonce="A" * 22,
        exposure_snapshot_digest="0" * 64,
    )


async def test_synthetic_gate_allows_consumed_challenge_without_accounting():
    gate = SyntheticDisclosureGate()
    assert isinstance(gate, DisclosureGate)
    decision = await gate.authorize_disclosure(challenge=_consumed(), snapshot=None)
    assert isinstance(decision, DisclosureDecision) and decision.allowed is True
    assert gate.accounted_records == 0


def test_disclosure_decision_is_frozen():
    decision = DisclosureDecision(allowed=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.allowed = False  # type: ignore[misc]


# --- AuditEvent mirrors Appendix C fields only ---

_EXPECTED_FIELDS = frozenset(
    {
        "event_id",
        "ts",
        "tool_name",
        "principal_ref",
        "client_ref",
        "account_ref",
        "peer_ref",
        "policy_epoch",
        "result_count",
        "duration_ms",
        "telegram_rpc_count",
        "status",
        "error_code",
    }
)


def _event() -> AuditEvent:
    return AuditEvent(
        event_id="evt_01J0000000000000000000000",
        ts="2026-09-21T00:31:12Z",
        tool_name="telegram_search_messages",
        principal_ref="prn_" + "a" * 26,
        client_ref="tcl_" + "b" * 26,
        account_ref="tga_" + "c" * 26,
        peer_ref=None,
        policy_epoch=7,
        result_count=7,
        duration_ms=931,
        telegram_rpc_count=3,
        status="ok",
        error_code=None,
    )


def test_audit_event_fields_mirror_appendix_c_only():
    assert {f.name for f in dataclasses.fields(AuditEvent)} == _EXPECTED_FIELDS


def test_audit_event_is_frozen_and_holds_no_bodies():
    event = _event()
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.status = "denied"  # type: ignore[misc]
    assert not hasattr(event, "query") and not hasattr(event, "body")


def test_no_chain_checkpoint_receipt_ledger_types():
    for name in ("AuditChain", "AuditCheckpoint", "DisclosureReceipt", "ExposureLedger"):
        assert not hasattr(audit_seam, name)


def test_emit_audit_appends_to_sink_and_logs_allowlist_only(caplog):
    get_audit_sink().clear()
    with caplog.at_level("INFO", logger="telegram_mcp.audit"):
        emit_audit(_event())
    assert get_audit_sink() == [_event()]
    assert "secret-body" not in caplog.text
