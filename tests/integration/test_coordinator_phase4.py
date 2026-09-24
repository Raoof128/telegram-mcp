# tests/integration/test_coordinator_phase4.py
"""Coordinator changes the real seams need (Phase-4 design §2.3)."""

from dataclasses import dataclass
from typing import Any

from comms.transports.telegram.disclosure.budget import call_binding_digest
from comms.transports.telegram.disclosure.coordinator import AuthorityRefusal
from tests.coordinator_fixtures import build_coordinator


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


async def test_the_reservation_binds_this_call(tmp_path):
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
    nonce = seen["request_nonce"]
    assert len(nonce) == 32 and int(nonce, 16) >= 0
    assert seen["binding_digest"] == call_binding_digest("telegram_get_messages", {}, nonce)


async def test_an_authority_refusal_stops_before_reservation(tmp_path):
    coordinator, conn, adapter = build_coordinator(tmp_path)

    def refuse(tool_name, request):
        raise AuthorityRefusal("POLICY_UNCONFIGURED")

    reserved: list[Any] = []
    coordinator._authority.snapshot = refuse
    coordinator._ledger.reserve = lambda **kwargs: reserved.append(kwargs)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )
    assert (outcome.released, outcome.error_code) == (False, "POLICY_UNCONFIGURED")
    assert reserved == []
    assert _count(conn, "disclosure_receipts") == 0


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
    from comms.transports.telegram.disclosure.measure import bytes_disclosed

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
