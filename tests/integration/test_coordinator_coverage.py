"""§23D/§14.1A at the coordinator: no search result is signed with inconsistent coverage.

This gate proves consistency (with itself, the cursor, meta.partial and the
records disclosed). The counters' truth comes from measurement upstream: the
work budget for RPCs, raw Telegram entries for hits examined, and the engine's
per-peer exhaustion for peers scanned (test_search_engine, test_search_reads).
"""

import hashlib

import pytest

from comms.transports.telegram.consent.challenge import jcs_dumps
from comms.transports.telegram.disclosure.budget import Usage, buckets_for
from comms.transports.telegram.disclosure.coverage import build_coverage
from tests.authority_fixtures import PROJECT_REF
from tests.coordinator_fixtures import build_coordinator

HIT = {
    "origin_project_refs": [PROJECT_REF],
    "peer_ref": "tgp_" + "b" * 26,
    "peer_display_name": "Ali",
    "message_ref": "tgm_" + "c" * 26,
    "sender_kind": "user",
    "sender_display_name": "Ali",
    "sent_at": "2026-09-23T00:00:00Z",
    "text": "needle",
    "text_truncated": False,
    "has_context": True,
}


def _coverage(**over):
    base = {
        "complete": True,
        "eligible_peers": 1,
        "peers_scanned": 1,
        "telegram_rpcs": 1,
        "hits_examined": 1,
        "hits_returned": 1,
        "partial_reasons": [],
        "project_coverage": [{"project_ref": PROJECT_REF, "eligible_peers": 1, "peers_scanned": 1}],
    }
    base.update(over)
    return build_coverage(**base)


class _Adapter:
    def __init__(self, side):
        self.side = side

    async def retrieve(self, *, tool_name, arguments, snapshot=None):
        return {
            "project": {"project_ref": PROJECT_REF, "display_name": "Alpha"},
            "results": [dict(HIT)],
            "search_scope": "project",
            **self.side,
        }


def _coordinator(tmp_path):
    coordinator, conn, _adapter = build_coordinator(tmp_path)
    data = {
        "project": {"project_ref": PROJECT_REF, "display_name": "Alpha"},
        "results": [HIT],
        "search_scope": "project",
    }
    coordinator._authority._worst_case = {
        key: Usage(usage.records + 5, usage.bytes + 2000)
        for key, usage in buckets_for("telegram_search_messages", data, client_id=1).items()
    }
    return coordinator, conn


async def test_consistent_coverage_is_signed_into_the_receipt(tmp_path):
    coordinator, conn = _coordinator(tmp_path)
    coverage = _coverage()
    outcome = await coordinator.disclose(
        tool_name="telegram_search_messages",
        arguments={},
        adapter=_Adapter({"_coverage": coverage}),
    )
    assert outcome.released and outcome.meta["coverage"] == coverage
    digest = conn.execute("SELECT canonical_coverage_digest FROM disclosure_receipts").fetchone()[0]
    assert digest == hashlib.sha256(jcs_dumps(coverage)).hexdigest()


@pytest.mark.parametrize(
    "side",
    [
        {},  # no coverage at all
        {"_coverage": _coverage(hits_returned=2)},  # does not describe the result
        {
            "_coverage": _coverage(complete=False, partial_reasons=["rpc_budget"]),
            "_next_cursor": "tgc_" + "a" * 26,
            "_partial": True,
        },  # a cursor without response_limit
        {"_coverage": _coverage(), "_partial": True},  # complete yet partial
    ],
)
async def test_inconsistent_coverage_is_never_signed(tmp_path, side):
    coordinator, conn = _coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_search_messages", arguments={}, adapter=_Adapter(side)
    )
    assert (outcome.released, outcome.error_code) == (False, "PROOF_GENERATION_FAILED")
    assert conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0] == 0
