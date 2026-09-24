"""Operator inspection (spec §23A.3, §23C; design §2.4, §2.6)."""

import time

import pytest

from comms.transports.telegram.disclosure.lineage import LineageVerdict, NoRestoreLineage
from comms.transports.telegram.ipc.handlers.inspect import inspect_handlers
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import insert_committed_receipt, seed_authority_rows


@pytest.fixture(autouse=True)
def _keys(tmp_path):
    from comms.transports.telegram.keys.store import provision_missing, set_store_dir

    store = tmp_path / "keys"
    provision_missing(store, phases=(2, 3))
    set_store_dir(store)


CLIENT = "tcl_" + "a" * 26
REF = "tdr_" + "a" * 26


class Broker:
    def pending_count(self):
        return 2


class Prompter:
    connected = True


class Unreconstructable:
    def affecting(self, conn, disclosure_ref):
        return LineageVerdict("payload_unreconstructable")


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    insert_committed_receipt(c, disclosure_ref=REF, records=3, size=120)
    return c


def _h(conn, lineage=None):
    return inspect_handlers(
        conn, lineage=lineage or NoRestoreLineage(), broker=Broker(), prompter=Prompter()
    )


def test_show_is_privacy_minimised_and_reports_signature_state(conn):
    shown = _h(conn)["disclosure show"]({"disclosure_ref": REF})
    assert shown["records_disclosed"] == 3 and shown["bytes_disclosed"] == 120
    assert shown["signature"] == "invalid"  # the fixture row carries a placeholder signature
    assert shown["lineage"] == "none"
    assert shown["delivery"] == "committed"
    assert set(shown) == {
        "disclosure_ref",
        "committed_at",
        "tool_name",
        "project_count",
        "effective_egress_level",
        "records_disclosed",
        "bytes_disclosed",
        "partial",
        "proof_key_id",
        "signature",
        "lineage",
        "delivery",
    }


def test_lineage_distinguishes_restore_from_tampering(conn):
    shown = _h(conn, Unreconstructable())["disclosure show"]({"disclosure_ref": REF})
    assert shown["lineage"] == "payload_unreconstructable"
    assert _h(conn, Unreconstructable())["disclosure verify"]({"disclosure_ref": REF}) == {
        "signature_valid": False,
        "payload_reconstructable": False,
        "lineage": "payload_unreconstructable",
        "delivery": "committed",
    }


def test_a_withheld_disclosure_says_so(conn):
    """Review #7: Phase 3's degraded state stays visible (accounted, never delivered)."""
    from comms.transports.telegram.disclosure.audit.anchor import latch_degraded

    latch_degraded(conn, reason="anchor_refresh_failure", disclosure_ref=REF)
    shown = _h(conn)["disclosure show"]({"disclosure_ref": REF})
    assert shown["delivery"] == "withheld_audit_unavailable"
    assert _h(conn)["disclosure verify"]({"disclosure_ref": REF})["delivery"] == (
        "withheld_audit_unavailable"
    )


def test_a_withheld_disclosure_stays_withheld_after_repair(conn):
    """After repair the latch clears, but the chained repair event still names the ref."""
    conn.execute(
        "INSERT INTO audit_events (event_id, ts, tool_name, status, disclosure_ref, chain_epoch,"
        " chain_seq, prev_event_mac, event_mac) VALUES ('evt_1', '2026-09-24T00:00:00Z',"
        " 'admin.repair_anchor', 'ok', ?, 1, 1, 'p', 'm')",
        (REF,),
    )
    conn.commit()
    assert _h(conn)["disclosure show"]({"disclosure_ref": REF})["delivery"] == (
        "withheld_audit_unavailable"
    )


def test_unknown_receipts_are_refused(conn):
    with pytest.raises(ValueError, match="unknown"):
        _h(conn)["disclosure show"]({"disclosure_ref": "tdr_" + "b" * 26})


def test_disclosure_key_without_a_published_key_says_so(conn):
    assert _h(conn)["disclosure key"]({}) == {"key": None}


def test_exposure_status_sums_the_window(conn):
    conn.execute(
        "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id, budget_subject_kind,"
        " budget_subject_digest, records_disclosed, bytes_disclosed, effective_egress_level)"
        " VALUES (?, ?, 1, 'client_global', ?, 3, 120, 'metadata_only')",
        (REF, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), _global_digest()),
    )
    conn.commit()
    status = _h(conn)["exposure status"]({"client_ref": CLIENT})
    only = status["clients"][0]
    assert only["client_ref"] == CLIENT
    assert only["client_global"]["records"] == 3 and only["client_global"]["bytes"] == 120
    assert only["client_global"]["hard_records"] == 1500
    assert "project" not in only


def test_exposure_status_filters_are_optional(conn):
    """Review #9: `exposure status [--client] [--project]`, both optional (spec §33)."""
    everyone = _h(conn)["exposure status"]({})
    assert [c["client_ref"] for c in everyone["clients"]] == [CLIENT]
    from tests.authority_fixtures import PROJECT_REF

    by_project = _h(conn)["exposure status"]({"project_ref": PROJECT_REF})
    assert by_project["clients"][0]["project"]["records"] == 0
    with pytest.raises(ValueError):
        _h(conn)["exposure status"]({"client_ref": "tcl_" + "z" * 26})


def _global_digest():
    from comms.transports.telegram.disclosure.budget import GLOBAL, subject_digest

    return subject_digest(GLOBAL)


def test_consent_status(conn):
    assert _h(conn)["consent status"]({}) == {"agent_connected": True, "pending": 2}
