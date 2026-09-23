"""4b coordinator changes: pre-retrieval authority, typed refusals, late partial."""

import asyncio

import pytest

from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, Usage, subject_digest
from telegram_mcp.disclosure.coordinator import DisclosureOutcome, RetrievalRefusal
from telegram_mcp.disclosure.seams import CoordinatorAuthority
from telegram_mcp.keys.store import load_key
from telegram_mcp.results import error_result
from telegram_mcp.runtime.identity import resolve_principal
from telegram_mcp.sensitive_dispatch import SensitiveDispatcher
from telegram_mcp.storage.db import bind_cursor_store
from tests.authority_fixtures import PROJECT_REF, seed_project_world
from tests.coordinator_fixtures import build_coordinator


def _global_key():
    return BucketKey(1, GLOBAL, subject_digest(GLOBAL))  # keyed: only after keys exist


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


async def test_authority_that_moved_during_consent_stops_before_any_rpc(tmp_path):
    coordinator, _conn, adapter = build_coordinator(tmp_path, moved="POLICY_CHANGED")
    adapter.calls.clear()
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    assert (outcome.released, outcome.error_code) == (False, "POLICY_CHANGED")
    assert adapter.calls == []
    assert coordinator._ledger.live_usage(_global_key()) == Usage(0, 0)


async def test_scope_mode_change_during_prompt_refuses(tmp_path):
    """Review Focus 1, against the real authority seam."""

    def real_authority(conn):
        seed_project_world(conn)
        return CoordinatorAuthority(
            conn,
            privacy_key=load_key("privacy-key"),
            cursor_key=load_key("cursor-key"),
            cursor_store=bind_cursor_store(conn),
            runtime_id=b"\x05" * 16,
        )

    coordinator, conn, adapter = build_coordinator(tmp_path, authority_factory=real_authority)
    adapter.calls.clear()
    real_consume = coordinator._consent.consume

    async def owner_flips_mode_while_prompted(challenge):
        conn.execute(
            "UPDATE policy_state SET mode = 'all_cloud_chats', policy_epoch = policy_epoch + 1"
        )
        conn.commit()
        return await real_consume(challenge)

    coordinator._consent.consume = owner_flips_mode_while_prompted
    principal = resolve_principal(conn, "tcl_" + "a" * 26)
    outcome = await coordinator.disclose(
        tool_name="telegram_list_chats",
        arguments={
            "project_ref": PROJECT_REF,
            "limit": 20,
            "chat_type": "any",
            "archived": "exclude",
        },
        adapter=adapter,
        principal=principal,
    )
    assert (outcome.released, outcome.error_code) == (False, "POLICY_CHANGED")
    assert adapter.calls == []
    assert _count(conn, "disclosure_receipts") == 0


async def test_a_flood_wait_is_a_retryable_refusal_that_charges_nothing(tmp_path):
    coordinator, conn, _adapter = build_coordinator(tmp_path)

    class Flooded:
        async def retrieve(self, *, tool_name, arguments, snapshot=None):
            raise RetrievalRefusal("FLOOD_WAIT", retry_after=7)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=Flooded()
    )
    assert (outcome.error_code, outcome.retry_after_seconds) == ("FLOOD_WAIT", 7)
    assert _count(conn, "exposure_ledger") == 0
    assert coordinator._ledger.live_usage(_global_key()) == Usage(0, 0)


async def test_cancellation_during_retrieval_propagates_and_releases(tmp_path):
    coordinator, conn, _adapter = build_coordinator(tmp_path)

    class Cancelled:
        async def retrieve(self, *, tool_name, arguments, snapshot=None):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await coordinator.disclose(
            tool_name="telegram_get_messages", arguments={}, adapter=Cancelled()
        )
    assert coordinator._ledger.live_usage(_global_key()) == Usage(0, 0)
    assert _count(conn, "disclosure_receipts") == 0


async def test_a_late_partial_reaches_the_receipt_and_meta(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)
    real = adapter.retrieve

    class Partial:
        async def retrieve(self, **kwargs):
            return {**(await real(**kwargs)), "_partial": True}

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=Partial()
    )
    assert outcome.released and outcome.meta["partial"] is True
    assert "_partial" not in outcome.data
    row = conn.execute("SELECT partial FROM disclosure_receipts").fetchone()
    assert row[0] == 1


def test_error_results_carry_retry_data():
    body = error_result("FLOOD_WAIT", retry_after_seconds=7).structured_content
    assert body["error"]["retryable"] is True and body["error"]["retry_after_seconds"] == 7
    assert error_result("NOT_ACCESSIBLE").structured_content["error"]["retryable"] is False


def test_retryability_is_the_frozen_table_and_nothing_else():
    from telegram_mcp.results import RETRYABILITY, _error_codes

    assert set(RETRYABILITY) == set(_error_codes())
    for code in (
        "CURSOR_EXPIRED",
        "CURSOR_POLICY_CHANGED",
        "CURSOR_PROJECT_CHANGED",
        "RESPONSE_LIMIT",
        "WORK_BUDGET_EXCEEDED",
        "FLOOD_WAIT",
        "TELEGRAM_UNAVAILABLE",
        "POLICY_CHANGED",
        "DEADLINE_EXCEEDED",
    ):
        assert error_result(code).structured_content["error"]["retryable"] is True, code
    # a caller cannot override a fixed row; it can only resolve a Maybe
    assert (
        error_result("AUTH_REQUIRED", retryable=True).structured_content["error"]["retryable"]
        is False
    )
    assert (
        error_result("EXPOSURE_BUDGET_EXCEEDED", retryable=True).structured_content["error"][
            "retryable"
        ]
        is True
    )
    assert error_result("INTERNAL_ERROR").structured_content["error"]["retryable"] is False


async def test_the_sensitive_dispatcher_passes_retry_data_through():
    async def disclose(**_kwargs):
        return DisclosureOutcome(
            released=False, error_code="FLOOD_WAIT", retryable=True, retry_after_seconds=3
        )

    class P:
        client_id = 1

    result = await SensitiveDispatcher(disclose).call("telegram_get_messages", {}, P())
    assert result.structured_content["error"]["retry_after_seconds"] == 3
