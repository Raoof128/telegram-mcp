"""The twelve-step disclosure transaction (design §2, §6.5)."""

from telegram_mcp.disclosure.coordinator import DISCLOSURE_STEPS


def test_the_twelve_steps_are_frozen_in_order():
    assert DISCLOSURE_STEPS == (
        "freeze_arguments",
        "snapshot_authority",
        "estimate_exposure",
        "consent_issue",
        "consent_consume",
        "reserve_budget",
        "retrieve",
        "revalidate_authority",
        "transform_egress",
        "measure_and_prepare_proof",
        "commit_disclosure",
        "refresh_anchor",
    )


def test_retrieval_never_happens_before_consent_is_consumed():
    # The security barrier: steps 1-6 must precede any adapter call.
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("consent_consume")
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("reserve_budget")


def test_the_anchor_is_the_last_step():
    assert DISCLOSURE_STEPS[-1] == "refresh_anchor"
    assert (
        DISCLOSURE_STEPS.index("commit_disclosure") == DISCLOSURE_STEPS.index("refresh_anchor") - 1
    )


import pytest

from telegram_mcp.storage.settings import get_setting
from tests.coordinator_fixtures import build_coordinator


def _counts(conn):
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("disclosure_receipts", "exposure_ledger", "audit_events")
    )


@pytest.mark.parametrize("crash_at", DISCLOSURE_STEPS[:10])
async def test_crashing_before_commit_leaves_nothing_durable(crash_at, tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, crash_at=crash_at)

    with pytest.raises(RuntimeError):
        await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)

    # Unreserved means undisclosed: nothing durable before step 11.
    assert _counts(conn) == (0, 0, 0)
    assert get_setting(conn, "audit.integrity_degraded") == 0


async def test_a_clean_call_commits_exactly_one_of_each(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.released
    receipts, ledger, events = _counts(conn)
    assert (receipts, events) == (1, 1)
    # Two dimensions for a single-project call: client_global plus the project.
    assert ledger == 2
    assert outcome.meta["disclosure"]["receipt_ref"] == outcome.disclosure_ref


async def test_crashing_between_commit_and_anchor_charges_and_withholds(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, crash_at="refresh_anchor")

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert not outcome.released
    assert outcome.error_code == "AUDIT_INTEGRITY_UNAVAILABLE"
    assert outcome.retryable is False
    # Accounted, and deliberately not refunded: a payload that may have been
    # produced is conservatively treated as charged.
    receipts, ledger, events = _counts(conn)
    assert (receipts, events) == (1, 1)
    assert ledger == 2
    assert get_setting(conn, "audit.integrity_degraded") == 1
    assert get_setting(conn, "audit.degraded_reason") == "anchor_refresh_failure"
    assert get_setting(conn, "audit.degraded_disclosure_ref").startswith("tdr_")


async def test_degraded_refuses_the_next_call_and_appends_nothing(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, crash_at="refresh_anchor")
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    before = _counts(conn)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.error_code == "AUDIT_INTEGRITY_UNAVAILABLE"
    # While degraded the anchor cannot advance, so nothing may append: a chain
    # that grows here becomes unrecoverable at startup. The refusal is
    # therefore NOT audited, and the degraded record is the evidence.
    assert _counts(conn) == before


async def test_consent_denial_retrieves_nothing(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, approve=False)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.error_code == "CONSENT_DENIED"
    assert _counts(conn) == (0, 0, 0)


async def test_one_snapshot_divergence_re_prompts_and_succeeds(tmp_path):
    coordinator, _conn, adapter = build_coordinator(tmp_path, diverge_times=1)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.released, "§23C.3 requires exactly one automatic re-prompt"


async def test_a_second_divergence_refuses_unretryably(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, diverge_times=2)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.error_code == "CONSENT_UNAVAILABLE"
    assert outcome.retryable is False, "an agent must not spin through prompts"
    assert _counts(conn) == (0, 0, 0)


async def test_authority_moving_after_retrieval_emits_nothing(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path, moved="SECURITY_LOCKED")

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.error_code == "SECURITY_LOCKED"
    assert outcome.data is None
    assert _counts(conn) == (0, 0, 0)
