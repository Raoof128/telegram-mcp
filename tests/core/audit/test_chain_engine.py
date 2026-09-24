"""comms v0.3 Task A4: the profile-parameterised chain engine in comms.core (design §A.5, R-002)."""

import ast
import json
import sqlite3
from pathlib import Path

import pytest
import sqlcipher3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core.audit import chain as engine
from comms.core.audit.chain import COMMS, ChainError
from comms.transports.telegram.disclosure.audit.profile import LEGACY_TELEGRAM

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "tests" / "fixtures" / "audit" / "legacy_chain_vectors.json").read_text()
)
KEY = b"\x33" * 32
COMMS_DDL = (
    (
        "CREATE TABLE audit_events (event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, subject_ref TEXT,"
        " subject_digest TEXT, payload TEXT NOT NULL, chain_epoch INTEGER NOT NULL, chain_seq INTEGER NOT NULL,"
        " prev_event_mac TEXT NOT NULL, event_mac TEXT NOT NULL, UNIQUE (chain_epoch, chain_seq))"
    ),
    (
        "CREATE TABLE audit_checkpoints (checkpoint_ref TEXT PRIMARY KEY, chain_epoch INTEGER NOT NULL,"
        " chain_seq INTEGER NOT NULL, last_event_id TEXT NOT NULL, last_event_mac TEXT NOT NULL, reason TEXT NOT NULL,"
        " created_at TEXT NOT NULL, signing_key_id TEXT NOT NULL, signature TEXT NOT NULL)"
    ),
)


def test_legacy_profile_reproduces_every_vector():
    key = bytes.fromhex(VECTORS["chain_key_hex"])
    assert engine.genesis_mac(LEGACY_TELEGRAM, 1) == VECTORS["genesis_mac"]["1"]
    assert engine.genesis_mac(LEGACY_TELEGRAM, 2) == VECTORS["genesis_mac"]["2"]
    for v in VECTORS["events"]:
        assert (
            engine.event_mac(
                LEGACY_TELEGRAM,
                key,
                chain_epoch=v["chain_epoch"],
                chain_seq=v["chain_seq"],
                prev_event_mac=v["prev_event_mac"],
                event=v["event"],
            )
            == v["event_mac"]
        )
    cp = VECTORS["checkpoint"]
    assert engine.checkpoint_message(LEGACY_TELEGRAM, cp["row"]).hex() == cp["message_hex"]


def _comms_event(n):
    return {
        "event_id": f"aev_{'a' * 25}{'abcdefg'[n % 7]}",
        "ts": f"2026-09-24T00:00:0{n}.000000Z",
        "kind": "system.test",
        "subject_ref": None,
        "subject_digest": None,
        "payload": "{}",
    }


def test_comms_profile_differs_from_legacy_for_identical_coordinates():
    assert engine.genesis_mac(COMMS, 1) != engine.genesis_mac(LEGACY_TELEGRAM, 1)
    assert COMMS.event_domain != LEGACY_TELEGRAM.event_domain
    assert COMMS.checkpoint_domain != LEGACY_TELEGRAM.checkpoint_domain


def _conn(driver):
    if driver == "sqlite3":
        conn = sqlite3.connect(":memory:", isolation_level=None)
    else:
        conn = sqlcipher3.connect(":memory:", isolation_level=None)
        conn.execute("PRAGMA key = \"x'" + "44" * 32 + "'\"")
    for ddl in COMMS_DDL:
        conn.execute(ddl)
    return conn


@pytest.mark.parametrize("driver", ["sqlite3", "sqlcipher3"])
def test_the_engine_runs_on_both_drivers(driver):
    conn = _conn(driver)
    conn.execute("BEGIN IMMEDIATE")
    for n in range(1, 5):
        engine.append_event(conn, COMMS, KEY, _comms_event(n))
    conn.execute("COMMIT")
    engine.verify_chain(conn, COMMS, lambda epoch: KEY)
    assert engine.head(conn, COMMS)["chain_seq"] == 4


def test_append_requires_an_open_transaction():
    conn = _conn("sqlite3")
    with pytest.raises(ChainError, match="transaction"):
        engine.append_event(conn, COMMS, KEY, _comms_event(1))


def test_append_refuses_an_event_out_of_profile():
    conn = _conn("sqlite3")
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ChainError):
        engine.append_event(conn, COMMS, KEY, {"event_id": "x"})
    conn.execute("ROLLBACK")


@pytest.mark.parametrize("driver", ["sqlite3", "sqlcipher3"])
def test_verify_detects_a_modified_row_and_a_deleted_middle_row(driver):
    conn = _conn(driver)
    conn.execute("BEGIN IMMEDIATE")
    for n in range(1, 6):
        engine.append_event(conn, COMMS, KEY, _comms_event(n))
    conn.execute("COMMIT")
    conn.execute("UPDATE audit_events SET payload = '[]' WHERE chain_seq = 3")
    with pytest.raises(ChainError):
        engine.verify_chain(conn, COMMS, lambda epoch: KEY)
    conn.execute("UPDATE audit_events SET payload = '{}' WHERE chain_seq = 3")
    engine.verify_chain(conn, COMMS, lambda epoch: KEY)
    conn.execute("DELETE FROM audit_events WHERE chain_seq = 3")
    with pytest.raises(ChainError):
        engine.verify_chain(conn, COMMS, lambda epoch: KEY)


def test_checkpoint_signs_the_head_and_verifies_by_recorded_key():
    conn = _conn("sqlite3")
    seed = b"\x55" * 32
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    conn.execute("BEGIN IMMEDIATE")
    engine.append_event(conn, COMMS, KEY, _comms_event(1))
    row = engine.insert_checkpoint(
        conn, COMMS, seed, now="2026-09-24T00:00:09.000000Z", reason="SCHEDULED"
    )
    conn.execute("COMMIT")
    assert row["reason"] == "SCHEDULED"
    engine.verify_checkpoints(conn, COMMS, lambda key_id: public)
    conn.execute("UPDATE audit_checkpoints SET reason = 'V0_3_CUTOVER'")
    with pytest.raises(ChainError):
        engine.verify_checkpoints(conn, COMMS, lambda key_id: public)


def test_no_private_immediate_transaction_exists():
    for path in (ROOT / "src" / "comms" / "core" / "audit").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            is_execute = (
                isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute"
            )
            if is_execute and node.args and isinstance(node.args[0], ast.Constant):
                sql = str(node.args[0].value).lstrip().upper()
                assert not sql.startswith(("BEGIN", "COMMIT", "ROLLBACK")), (path, node.lineno)


def test_one_append_guard_per_profile():
    assert engine.append_guard(COMMS) is engine.append_guard(COMMS)
    assert engine.append_guard(COMMS) is not engine.append_guard(LEGACY_TELEGRAM)
