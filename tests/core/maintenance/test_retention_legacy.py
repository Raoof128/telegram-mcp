"""comms v0.3 Task B17: the retention runner and its legacy phases (A15, G6, N8).

The legacy history: e1 (disclosure R_OLD, August), a signed checkpoint at e2 (1 September),
e3, e4 (disclosure R_NEW, recent); then the cutover seals it. Exposure rows exist for both.
"""

import sqlite3

import pytest

from comms.core.audit import cutover
from comms.core.audit.integrity import AuditIntegrityDegraded, latch_degraded
from comms.core.audit.verify_all import verify_all
from comms.core.maintenance.retention import RetentionPolicy, run_retention
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit.chain import append_event, insert_checkpoint
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.authority_fixtures import insert_committed_receipt, seed_authority_rows
from tests.core.audit.legacy_fixtures import (
    CHAIN_KEY,
    CHECKPOINT_SEED,
    comms_world,
    legacy_event,
    legacy_port,
    public_for,
    verify_keys,
)
from tests.core.campaign_helpers import NOW

R_OLD, R_NEW = "tdr_" + "o" * 26, "tdr_" + "n" * 26
KEEP_ALL = RetentionPolicy(
    exposure_ledger_days=3650,
    receipt_days=3650,
    message_ref_days=3650,
    audit_events_days=3650,
    campaign_body_days=3650,
    identity_retention_days=3650,
)


def _history(conn):
    seed_authority_rows(conn)
    insert_committed_receipt(
        conn, disclosure_ref=R_OLD, records=1, size=1, committed_at="2026-08-01T00:00:00Z"
    )
    insert_committed_receipt(
        conn, disclosure_ref=R_NEW, records=1, size=1, committed_at="2026-09-23T00:00:00Z"
    )
    for ref, ts in ((R_OLD, "2026-08-01T00:00:00Z"), (R_NEW, "2026-09-23T00:00:00Z")):
        conn.execute(
            "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id, budget_subject_kind,"
            " budget_subject_digest, records_disclosed, bytes_disclosed, effective_egress_level)"
            " VALUES (?, ?, 1, 'client_global', ?, 1, 1, 'metadata_only')",
            (ref, ts, "d" * 64),
        )
    conn.commit()
    for ref, stamp in ((R_OLD, "2026-08-01T00:00:00Z"), (None, "2026-08-02T00:00:00Z")):
        with write_tx(conn):
            append_event(
                conn,
                CHAIN_KEY,
                {**legacy_event("telegram_get_messages"), "disclosure_ref": ref, "ts": stamp},
            )
    with write_tx(conn):
        insert_checkpoint(conn, CHECKPOINT_SEED, now="2026-09-01T00:00:00Z")
    for ref in (None, R_NEW):
        with write_tx(conn):
            append_event(
                conn, CHAIN_KEY, {**legacy_event("telegram_get_messages"), "disclosure_ref": ref}
            )


@pytest.fixture
def env(tmp_path):
    world = comms_world(tmp_path)
    world["port"] = legacy_port(tmp_path, events=0, name="history.db", build=_history)
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    world["legacy"] = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    return world


def _run(env, **days):
    policy = RetentionPolicy(**{**KEEP_ALL.__dict__, **days})
    return run_retention(env["conn"], env["legacy"], policy, env["writer"], now=NOW)


def _count(env, table):
    return env["port"].conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_receipts_become_purgeable_only_after_legacy_truncation(env):
    report = _run(env, exposure_ledger_days=30, receipt_days=30)  # the chain keeps e1
    assert report.phases["legacy_receipts"] == 0 and _count(env, "disclosure_receipts") == 2
    report = _run(env, exposure_ledger_days=30, receipt_days=30, audit_events_days=10)
    assert report.phases["legacy_chain"] == 1  # e1, strictly before the root at e2
    assert report.phases["legacy_receipts"] == 1
    remaining = {
        r[0] for r in env["port"].conn.execute("SELECT disclosure_ref FROM disclosure_receipts")
    }
    assert remaining == {R_NEW}


def test_final_cutover_checkpoint_and_lineage_survive_legacy_truncation(env):
    final_before = env["port"].sealed_record()
    report = _run(
        env, audit_events_days=0
    )  # the latest root at or before now: the final checkpoint
    assert report.roots["legacy"] is not None
    assert env["port"].sealed_record() == final_before
    marker = env["port"].conn.execute(
        "SELECT count(*) FROM audit_events WHERE tool_name = 'system.cutover_final'"
    )
    assert marker.fetchone()[0] == 1
    assert env["conn"].execute("SELECT count(*) FROM audit_lineage").fetchone()[0] == 1


def test_verify_all_green_after_legacy_truncation(env):
    _run(env, audit_events_days=10)
    report = verify_all(env["conn"], env["port"].conn, verify_keys(env))
    assert report.problems == () and report.ok


def test_ad_hoc_legacy_audit_delete_refused_by_trigger(env):
    for table in ("audit_events", "audit_checkpoints"):
        with pytest.raises(sqlite3.IntegrityError, match="truncation"):
            env["port"].conn.execute(f"DELETE FROM {table}")
        env["port"].conn.rollback()


def test_message_refs_referenced_by_a_live_cursor_survive(env):
    conn = env["port"].conn
    conn.execute(
        "INSERT INTO peers (account_id, peer_ref, telegram_peer_type, telegram_peer_id, first_seen_at, last_seen_at)"
        " VALUES (1, ?, 'user', 5, '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')",
        ("tpe_" + "p" * 26,),
    )
    for n, ref in enumerate(("tmr_" + "a" * 26, "tmr_" + "b" * 26), start=1):
        conn.execute(
            "INSERT INTO message_refs (account_id, peer_id, message_ref, telegram_message_id, minted_at, last_used_at)"
            " VALUES (1, 1, ?, ?, '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')",
            (ref, n),
        )
    conn.execute(
        "INSERT INTO cursors (cursor_ref, principal_id, client_id, account_id, security_epoch, policy_epoch,"
        " project_scope_digest, tool_name, query_digest, state_json, created_at, expires_at)"
        " VALUES (?, 1, 1, 1, 1, 1, 'd', 'telegram_get_messages', 'q', ?, '2026-09-23T00:00:00Z', '2026-09-25T00:00:00Z')",
        ("tcu_" + "c" * 26, '{"anchor":"' + "tmr_" + "a" * 26 + '"}'),
    )
    conn.commit()
    report = _run(env, message_ref_days=30)
    assert report.phases["legacy_message_refs"] == 1
    assert {r[0] for r in conn.execute("SELECT message_ref FROM message_refs")} == {
        "tmr_" + "a" * 26
    }


def test_refuses_while_degraded(env):
    latch_degraded(env["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    with pytest.raises(AuditIntegrityDegraded):
        _run(env, exposure_ledger_days=0)
    assert _count(env, "exposure_ledger") == 2


def test_counts_per_phase_reported(env):
    report = _run(
        env, exposure_ledger_days=30, receipt_days=30, audit_events_days=10, message_ref_days=30
    )
    assert set(report.phases) == {
        "legacy_exposure",
        "legacy_chain",
        "legacy_receipts",
        "legacy_message_refs",
        "comms_chain",  # B18
    }
    assert report.phases["legacy_exposure"] == 1
    assert report.outcome == "ok"
