"""The ten-step disclosure transaction (design §2, §6.5)."""

from comms.transports.telegram.disclosure.coordinator import DISCLOSURE_STEPS


def test_the_ten_steps_are_frozen_in_order():
    assert DISCLOSURE_STEPS == (
        "freeze_arguments",
        "snapshot_authority",
        "estimate_exposure",
        "reserve_budget",
        "retrieve",
        "revalidate_authority",
        "transform_egress",
        "measure_and_prepare_proof",
        "commit_disclosure",
        "refresh_anchor",
    )


def test_retrieval_never_happens_before_the_reservation():
    # The security barrier: steps 1-4 must precede any adapter call.
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("reserve_budget")


def test_the_anchor_is_the_last_step():
    assert DISCLOSURE_STEPS[-1] == "refresh_anchor"
    assert (
        DISCLOSURE_STEPS.index("commit_disclosure") == DISCLOSURE_STEPS.index("refresh_anchor") - 1
    )


import pytest

from comms.transports.telegram.storage.settings import get_setting
from tests.coordinator_fixtures import build_coordinator


def _counts(conn):
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("disclosure_receipts", "exposure_ledger", "audit_events")
    )


@pytest.mark.parametrize(
    "crash_at", DISCLOSURE_STEPS[: DISCLOSURE_STEPS.index("commit_disclosure")]
)
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


async def test_authority_moving_after_retrieval_emits_nothing(tmp_path):
    coordinator, conn, adapter = build_coordinator(
        tmp_path, moved="SECURITY_LOCKED", moved_on_call=2
    )
    adapter.calls.clear()

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    assert adapter.calls == ["telegram_get_messages"]  # step 8, after retrieval

    assert outcome.error_code == "SECURITY_LOCKED"
    assert outcome.data is None
    assert _counts(conn) == (0, 0, 0)
