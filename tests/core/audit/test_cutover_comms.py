"""comms v0.3 Task A11: the cutover's comms side — lineage, genesis event, anchor, tgml1 retirement."""

import json
import sqlite3

import pytest
import sqlcipher3

from comms.core.audit import cutover as co
from comms.core.audit.anchor import COMMS_ANCHOR, read_anchor
from comms.core.audit.chain import COMMS, genesis_mac, head, verify_chain
from comms.core.keys.slots import load_active
from comms.transports.telegram.ipc.leases import LeaseError, mint_lease, verify_lease
from comms.transports.telegram.keys.store import read_lease_seed
from comms.transports.telegram.storage.authority_view import load_security
from tests.core.audit.legacy_fixtures import CLIENT, comms_world, legacy_port
from tests.core.campaign_helpers import NOW


@pytest.fixture
def env(tmp_path):
    return comms_world(tmp_path, bearer=True)


def _events(conn, kind=None):
    rows = conn.execute(
        "SELECT chain_seq, kind, subject_ref, subject_digest, payload FROM audit_events ORDER BY chain_seq"
    ).fetchall()
    return [r for r in rows if kind is None or r[1] == kind]


def _lineage(conn):
    cur = conn.execute("SELECT * FROM audit_lineage")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _phase(conn):
    return co.current_phase(conn)[0]


def test_first_comms_event_is_system_audit_cutover_bound_to_the_legacy_digest(env):
    assert co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW) == "COMPLETE"
    seal = env["port"].sealed_record()
    (lineage,) = _lineage(env["conn"])
    first = _events(env["conn"])[0]
    assert first[0] == 1 and first[1] == "system.audit_cutover"
    assert first[2] == co.current_phase(env["conn"])[1] == lineage["cutover_ref"]
    assert first[3] == lineage["lineage_digest"]
    payload = json.loads(first[4])
    assert payload == {
        "legacy_checkpoint_digest": seal.checkpoint_digest,
        "legacy_final_epoch": seal.final_epoch,
        "legacy_final_head": seal.final_head,
        "legacy_checkpoint_key_id": seal.checkpoint_key_id,
        "comms_chain_domain": COMMS.event_domain.rstrip(b"\0").decode(),
        "comms_epoch": 1,
        "comms_audit_key_id": load_active(env["conn"], env["store"], "audit-chain-key")[1],
    }
    assert lineage["legacy_checkpoint_digest"] == seal.checkpoint_digest
    assert lineage["legacy_chain_domain"] == env["port"].chain_domain
    assert lineage["comms_genesis_digest"] == genesis_mac(COMMS, 1)
    assert lineage["comms_first_epoch"] == 1


def test_lineage_digest_covers_every_column(env):
    co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW)
    (row,) = _lineage(env["conn"])
    stored = row.pop("lineage_digest")
    assert co.lineage_digest(row) == stored
    for column, value in row.items():
        changed = dict(row, **{column: (value + 1) if isinstance(value, int) else value + "x"})
        assert co.lineage_digest(changed) != stored, column
    with pytest.raises(ValueError, match="lineage columns"):
        co.lineage_digest({k: v for k, v in row.items() if k != "created_at"})


def test_the_comms_anchor_is_at_the_exact_head_after_genesis(env):
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(
            env["conn"], env["port"], env["writer"], now=NOW, crash_at="after_COMMS_ANCHORED"
        )
    assert _phase(env["conn"]) == "COMMS_ANCHORED"
    anchored = read_anchor(COMMS_ANCHOR, env["anchor"], env["keys"].for_epoch(1))
    current = head(env["conn"], COMMS)
    assert (anchored["chain_seq"], anchored["event_mac"]) == (1, current["event_mac"])


def test_resume_after_genesis_reuses_and_never_duplicates(env):
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(
            env["conn"], env["port"], env["writer"], now=NOW, crash_at="after_COMMS_GENESIS"
        )
    assert _phase(env["conn"]) == "COMMS_GENESIS"
    assert co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW) == "COMPLETE"
    assert co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW) == "COMPLETE"
    assert len(_lineage(env["conn"])) == 1
    assert len(_events(env["conn"], "system.audit_cutover")) == 1
    assert len(_events(env["conn"], "system.legacy_client_auth_revoked")) == 1
    verify_chain(env["conn"], COMMS, env["keys"].for_epoch)  # raises on any broken link


def test_conflicting_legacy_digest_for_the_same_cut_ref_fails_closed(env):
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(
            env["conn"], env["port"], env["writer"], now=NOW, crash_at="after_COMMS_GENESIS"
        )
    other = legacy_port(env["tmp"], events=5, name="other.db")
    other.seal(now=NOW)
    with pytest.raises(co.CutoverError, match="lineage conflict"):
        co.run_cutover(env["conn"], other, env["writer"], now=NOW)
    assert _phase(env["conn"]) == "COMMS_GENESIS"
    assert len(_events(env["conn"])) == 1


def test_a_lineage_row_that_disagrees_with_the_seal_fails_closed(env):
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(
            env["conn"], env["port"], env["writer"], now=NOW, crash_at="after_COMMS_GENESIS"
        )
    env["conn"].execute("DROP TRIGGER audit_lineage_immutable_u")
    env["conn"].execute("UPDATE audit_lineage SET legacy_final_epoch = 99")
    env["conn"].commit()
    with pytest.raises(co.CutoverError, match="lineage conflict"):
        co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW)
    assert _phase(env["conn"]) == "COMMS_GENESIS"


def test_complete_is_reached_only_after_every_tgml1_seed_is_revoked_and_epoch_bumped(env):
    port = env["port"]
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(env["conn"], port, env["writer"], now=NOW, crash_at="after_COMMS_ANCHORED")
    assert read_lease_seed(port.key_dir, CLIENT) is not None
    assert load_security(port.conn)[0] == 1
    assert co.run_cutover(env["conn"], port, env["writer"], now=NOW) == "COMPLETE"
    assert list(port.key_dir.glob("lease-seed.*")) == []
    assert load_security(port.conn)[0] == 2
    assert port.conn.execute("SELECT enabled FROM mcp_clients").fetchone()[0] == 0
    (revoked,) = _events(env["conn"], "system.legacy_client_auth_revoked")
    assert json.loads(revoked[4]) == {"revoked_count": 1, "security_epoch": 2}
    assert revoked[2] == co.current_phase(env["conn"])[1]


def test_a_tgml1_bearer_is_refused_after_complete(env):
    port = env["port"]
    token = mint_lease(
        seed=read_lease_seed(port.key_dir, CLIENT), client=CLIENT, epoch=1, now=1_000
    )
    co.run_cutover(env["conn"], port, env["writer"], now=NOW)
    seeds = {CLIENT: bytes(32)}  # even a verifier still holding the old seed refuses it
    with pytest.raises(LeaseError, match="security epoch"):
        verify_lease(token, seeds=seeds, epoch=load_security(port.conn)[0], now=1_000)
    with pytest.raises(sqlite3.IntegrityError, match="tgml1 is retired"):
        port.conn.execute("UPDATE mcp_clients SET enabled = 1")
    port.conn.rollback()


def test_revocation_is_idempotent_on_resume(env):
    port = env["port"]
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(env["conn"], port, env["writer"], now=NOW, crash_at="after_legacy_revoke")
    assert _phase(env["conn"]) == "COMMS_ANCHORED"
    assert load_security(port.conn)[0] == 2
    assert co.run_cutover(env["conn"], port, env["writer"], now=NOW) == "COMPLETE"
    assert load_security(port.conn)[0] == 2
    (revoked,) = _events(env["conn"], "system.legacy_client_auth_revoked")
    assert json.loads(revoked[4]) == {"revoked_count": 1, "security_epoch": 2}


def test_run_cutover_end_to_end_reaches_complete(env):
    assert co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW) == "COMPLETE"
    assert [e[1] for e in _events(env["conn"])] == [
        "system.audit_cutover",
        "system.legacy_client_auth_revoked",
    ]
    verify_chain(env["conn"], COMMS, env["keys"].for_epoch)  # raises on any broken link
    anchored = read_anchor(COMMS_ANCHOR, env["anchor"], env["keys"].for_epoch(1))
    assert anchored["chain_seq"] == 2
    with pytest.raises(sqlcipher3.IntegrityError, match="exact next state"):
        env["conn"].execute("UPDATE cutover_state SET phase = 'NONE'")
    env["conn"].rollback()


def test_a_comms_chain_with_events_before_genesis_refuses(env):
    with env["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    with pytest.raises(co.CutoverError, match="comms chain is not empty"):
        co.run_cutover(env["conn"], env["port"], env["writer"], now=NOW)
    assert _phase(env["conn"]) == "LEGACY_ANCHORED" and _lineage(env["conn"]) == []
