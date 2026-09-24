"""comms v0.3 Task B10: retired HMAC secrets are kept only while a proven dependency exists (A10)."""

from datetime import timedelta

import pytest

from comms.core.keys.retired import RetiredKeyError, destroy_retired_hmac, retired_hmac_status
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate
from tests.authority_fixtures import insert_committed_receipt, seed_authority_rows
from tests.core.campaign_helpers import NOW

WINDOW = 60  # minutes


@pytest.fixture
def legacy(tmp_path):
    conn = open_db(tmp_path / "legacy.db")
    migrate(conn)
    return conn


def _exposure(conn, *, minutes_ago):
    seed_authority_rows(conn)
    ref = "tdr_" + "q" * 26
    insert_committed_receipt(conn, disclosure_ref=ref, records=1, size=1)
    ts = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id, budget_subject_kind,"
        " budget_subject_digest, records_disclosed, bytes_disclosed, effective_egress_level)"
        " VALUES (?, ?, 1, 'client_global', ?, 1, 1, 'metadata_only')",
        (ref, ts, "d" * 64),
    )
    conn.commit()


def _status(conn):
    return retired_hmac_status(conn, window_minutes=WINDOW, now=NOW)


def test_both_are_destroyable_on_an_empty_legacy_db(legacy):
    assert _status(legacy) == {"principal-key": "destroyable", "privacy-key": "destroyable"}


def test_privacy_key_destroyable_once_no_exposure_row_in_window(legacy):
    _exposure(legacy, minutes_ago=WINDOW - 1)
    assert _status(legacy)["privacy-key"] == "dependency_proven"
    legacy.execute(
        "UPDATE exposure_ledger SET ts = ?",
        ((NOW - timedelta(minutes=WINDOW + 1)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    legacy.commit()
    assert _status(legacy)["privacy-key"] == "destroyable"


def test_principal_key_kept_while_a_dependent_row_exists(legacy):
    seed_authority_rows(legacy)
    assert _status(legacy)["principal-key"] == "dependency_proven"


def test_destroy_refuses_while_dependency_proven(legacy):
    _exposure(legacy, minutes_ago=1)
    calls = []
    with pytest.raises(RetiredKeyError, match="dependency"):
        destroy_retired_hmac(
            legacy, "privacy-key", lambda: calls.append(1), window_minutes=WINDOW, now=NOW
        )
    assert calls == []


def test_destroy_runs_once_destroyable_and_refuses_other_purposes(legacy):
    calls = []
    destroy_retired_hmac(
        legacy, "privacy-key", lambda: calls.append(1), window_minutes=WINDOW, now=NOW
    )
    assert calls == [1]
    with pytest.raises(RetiredKeyError, match="retired HMAC"):
        destroy_retired_hmac(
            legacy, "audit-chain-key", lambda: calls.append(2), window_minutes=WINDOW, now=NOW
        )
    assert calls == [1]
