"""comms v0.3 Task A12: `audit verify --all` walks legacy → lineage → comms and fails closed (A6, G6)."""

import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core.audit import cutover as co
from comms.core.audit.verify_all import VerifyKeys, verify_all
from comms.core.audit.writer import AuditWriter, SlotChainKeys
from comms.core.keys import ids
from comms.core.keys.slots import KeySlotStore, bootstrap_comms_audit_keys
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit import chain as legacy_chain
from comms.transports.telegram.runtime.cutover_barrier import legacy_verifier
from comms.transports.telegram.storage.settings import set_setting
from tests.core import schema_fixtures as fx
from tests.core.audit.legacy_fixtures import CHAIN_KEY, CHECKPOINT_SEED, legacy_port
from tests.core.campaign_helpers import NOW

_PUBLIC = Ed25519PrivateKey.from_private_bytes(CHECKPOINT_SEED).public_key().public_bytes_raw()


def _public_for(key_id):
    return _PUBLIC if key_id == ids.ed25519_key_id(_PUBLIC) else None


@pytest.fixture
def world(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    adir = tmp_path / "comms-anchor"
    adir.mkdir(mode=0o700)
    os.chmod(adir, 0o700)
    keys = SlotChainKeys(conn, store)
    writer = AuditWriter(conn, keys, adir / "head.anchor", clock=lambda: NOW)
    return {
        "conn": conn,
        "keys": keys,
        "writer": writer,
        "port": legacy_port(tmp_path),
        "tmp": tmp_path,
    }


def _keys(world):
    return VerifyKeys(
        legacy=legacy_verifier(CHAIN_KEY, _public_for),
        comms_key_for_epoch=world["keys"].for_epoch,
        comms_anchor_path=world["writer"]._anchor,
    )


def _verify(world, legacy_conn=None):
    return verify_all(world["conn"], legacy_conn or world["port"].conn, _keys(world))


def test_all_green_after_run_cutover(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    report = _verify(world)
    assert report.problems == ()
    assert (report.legacy, report.lineage, report.comms, report.ok) == ("ok", "ok", "ok", True)


def test_before_any_cutover_the_report_fails_closed(world):
    report = _verify(world)
    assert not report.ok
    assert "LEGACY_SEAL_MISSING" in report.problems and "LINEAGE_MISSING" in report.problems


def test_genesis_without_matching_seal_is_integrity_failure(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    other = legacy_port(world["tmp"], events=4, name="other.db")
    other.seal(now=NOW)
    report = _verify(world, legacy_conn=other.conn)
    assert not report.ok and report.lineage == "fail"
    assert "LINEAGE_SEAL_MISMATCH" in report.problems


def test_seal_bound_to_two_geneses_fails(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    conn = world["conn"]
    cur = conn.execute(f"SELECT {', '.join(co.LINEAGE_COLUMNS)} FROM audit_lineage")
    row = dict(zip(co.LINEAGE_COLUMNS, cur.fetchone(), strict=True))
    row["cutover_ref"] = "cut_" + "b" * 26
    names = (*co.LINEAGE_COLUMNS, "lineage_digest")
    with write_tx(conn):
        conn.execute(
            f"INSERT INTO audit_lineage ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            (*row.values(), co.lineage_digest(row)),
        )
    report = _verify(world)
    assert not report.ok and "LINEAGE_DUPLICATE" in report.problems


def test_missing_lineage_fails(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    conn = world["conn"]
    conn.execute("DROP TRIGGER audit_lineage_immutable_d")
    with write_tx(conn):
        conn.execute("DELETE FROM audit_lineage")
    report = _verify(world)
    assert not report.ok and report.lineage == "fail" and "LINEAGE_MISSING" in report.problems


def test_a_tampered_genesis_payload_fails(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    conn = world["conn"]
    payload = json.loads(
        conn.execute("SELECT payload FROM audit_events WHERE chain_seq = 1").fetchone()[0]
    )
    payload["legacy_final_epoch"] = 7
    conn.execute("DROP TRIGGER audit_events_append_only_u")
    with write_tx(conn):
        conn.execute(
            "UPDATE audit_events SET payload = ? WHERE chain_seq = 1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
    report = _verify(world)
    assert not report.ok
    assert {"GENESIS_MISMATCH", "COMMS_CHAIN_INVALID"} <= set(report.problems)


def test_a_stale_comms_anchor_fails(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    anchor = world["writer"]._anchor
    before = anchor.read_bytes()
    with world["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    anchor.write_bytes(before)
    report = _verify(world)
    assert not report.ok and "COMMS_ANCHOR_MISMATCH" in report.problems


def test_rolled_back_legacy_db_fails(world):
    snapshot = world["tmp"] / "legacy-before-seal.db"
    world["port"].conn.execute("VACUUM INTO ?", (str(snapshot),))
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    restored = legacy_port(world["tmp"], events=0, name="legacy-before-seal.db")
    report = _verify(world, legacy_conn=restored.conn)
    assert not report.ok and report.legacy == "fail" and "LEGACY_SEAL_MISSING" in report.problems


def _truncate_legacy_before(conn, seq):
    conn.execute("DELETE FROM audit_events WHERE chain_epoch = 1 AND chain_seq < ?", (seq,))
    conn.commit()


def test_legacy_chain_truncated_at_a_signed_root_still_verifies_all(world):
    port = world["port"]
    with write_tx(port.conn):
        legacy_chain.insert_checkpoint(port.conn, CHECKPOINT_SEED, now="2026-09-24T00:00:00Z")
    co.run_cutover(world["conn"], port, world["writer"], now=NOW)
    _truncate_legacy_before(port.conn, 3)  # the root is the checkpoint at seq 3
    report = _verify(world)
    assert report.problems == () and report.ok


def test_legacy_truncation_without_a_root_fails(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    _truncate_legacy_before(world["port"].conn, 2)  # no checkpoint signs seq 2
    report = _verify(world)
    assert not report.ok and "LEGACY_CHAIN_INVALID" in report.problems


def test_legacy_truncation_that_removed_the_final_checkpoint_fails(world):
    port = world["port"]
    co.run_cutover(world["conn"], port, world["writer"], now=NOW)
    port.conn.execute(
        "DELETE FROM audit_checkpoints WHERE chain_seq = (SELECT max(chain_seq) FROM audit_checkpoints)"
    )
    port.conn.commit()
    report = _verify(world)
    assert not report.ok and "LEGACY_SEAL_MISSING" in report.problems


def test_a_legacy_db_forged_to_look_sealed_without_the_cutover_marker_fails(world):
    co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    forged = legacy_port(world["tmp"], events=3, name="forged.db")
    with write_tx(forged.conn):
        legacy_chain.insert_checkpoint(forged.conn, CHECKPOINT_SEED, now="2026-09-24T00:00:00Z")
    set_setting(forged.conn, "audit.append_state", "sealed")
    report = _verify(world, legacy_conn=forged.conn)
    assert not report.ok and "LEGACY_SEAL_MISSING" in report.problems
