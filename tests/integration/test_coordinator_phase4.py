# tests/integration/test_coordinator_phase4.py
"""Coordinator changes the real seams need (Phase-4 design §2.3)."""

from dataclasses import dataclass
from typing import Any

from telegram_mcp.disclosure.coordinator import AuthorityRefusal, ConsentRefusal
from tests.coordinator_fixtures import build_coordinator


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


async def test_the_reservation_binds_the_approval_nonce(tmp_path):
    coordinator, _conn, adapter = build_coordinator(tmp_path)
    seen: dict[str, Any] = {}
    real_reserve = coordinator._ledger.reserve

    def spy(**kwargs):
        seen.update(kwargs)
        return real_reserve(**kwargs)

    coordinator._ledger.reserve = spy
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    assert outcome.released
    assert seen["request_nonce"] == "N" * 22


async def test_issue_is_awaited_and_sees_the_projection(tmp_path):
    coordinator, _conn, adapter = build_coordinator(tmp_path)
    await coordinator.disclose(tool_name="telegram_get_messages", arguments={}, adapter=adapter)
    issued = coordinator._consent.issued
    assert len(issued) == 1 and issued[0]["tier"] == "normal"
    assert issued[0]["projected"], "the prompt must see the projected buckets"


async def test_an_authority_refusal_stops_before_consent(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)

    def refuse(tool_name, request):
        raise AuthorityRefusal("POLICY_UNCONFIGURED")

    coordinator._authority.snapshot = refuse
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    assert (outcome.released, outcome.error_code) == (False, "POLICY_UNCONFIGURED")
    assert coordinator._consent.issued == []
    assert _count(conn, "disclosure_receipts") == 0


async def test_a_consent_refusal_carries_its_code(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)

    async def unavailable(challenge):
        raise ConsentRefusal("CONSENT_UNAVAILABLE")

    coordinator._consent.consume = unavailable
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    assert (outcome.released, outcome.error_code) == (False, "CONSENT_UNAVAILABLE")
    assert _count(conn, "exposure_ledger") == 0


@dataclass
class _SidecarAdapter:
    extra: dict[str, Any]

    async def retrieve(self, *, tool_name, arguments, snapshot=None):
        return {
            "project": {"project_ref": "tpr_" + "a" * 26},
            "messages": [
                {
                    "message_ref": "tgm_" + "a" * 26,
                    "origin_project_refs": ["tpr_" + "a" * 26],
                    "text": "hello",
                    "text_truncated": False,
                }
            ],
            **self.extra,
        }


async def test_side_keys_reach_meta_and_are_never_measured(tmp_path):
    coordinator, conn, _adapter = build_coordinator(tmp_path)
    cursor = "tgc_" + "b" * 26
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages",
        arguments={},
        adapter=_SidecarAdapter({"_next_cursor": cursor}),
    )
    assert outcome.released
    assert outcome.meta["next_cursor"] == cursor
    assert "_next_cursor" not in outcome.data
    from telegram_mcp.disclosure.measure import bytes_disclosed

    receipt_bytes = conn.execute("SELECT bytes_disclosed FROM disclosure_receipts").fetchone()[0]
    assert receipt_bytes == bytes_disclosed(outcome.data)


async def test_meta_partial_is_the_signed_partial(tmp_path):
    coordinator, _conn, _adapter = build_coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages",
        arguments={},
        adapter=_SidecarAdapter({"_next_cursor": "tgc_" + "b" * 26}),
    )
    assert (
        outcome.meta["partial"] is outcome.meta["disclosure"]["proof_payload"]["partial"] is False
    )


async def test_an_unknown_side_key_fails_closed(tmp_path):
    coordinator, conn, _adapter = build_coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages",
        arguments={},
        adapter=_SidecarAdapter({"_debug": "x"}),
    )
    assert (outcome.released, outcome.error_code) == (False, "INTERNAL_ERROR")
    assert _count(conn, "disclosure_receipts") == 0


async def test_catalogue_tools_are_gateway_metadata(tmp_path):
    coordinator, _conn, _adapter = build_coordinator(tmp_path)

    class Catalogue:
        async def retrieve(self, *, tool_name, arguments, snapshot=None):
            return {"projects": []}

    outcome = await coordinator.disclose(
        tool_name="telegram_list_projects", arguments={}, adapter=Catalogue()
    )
    assert outcome.released
    assert outcome.meta["source"] == "gateway"
    assert outcome.meta["content_trust"] == "non_instructional_gateway_metadata"
