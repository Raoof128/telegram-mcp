"""comms v0.3 Task A8: AuditWriter — serialized commit → exact-head anchor; complete-row journal binding."""

import ast
import os
import threading
from pathlib import Path

import pytest
import sqlcipher3

from comms.core.audit import anchor as anchor_mod
from comms.core.audit import writer as writer_mod
from comms.core.audit.anchor import COMMS_ANCHOR, read_anchor
from comms.core.audit.chain import COMMS, head, verify_chain
from comms.core.audit.writer import AnchorFailed, AuditWriter, SlotChainKeys, journal_digest
from comms.core.campaigns.events import append_event
from comms.core.keys.slots import KeySlotStore, bootstrap_comms_audit_keys
from comms.core.storage.db import open_comms_db
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def env(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    adir = tmp_path / "anchor"
    adir.mkdir(mode=0o700)
    os.chmod(adir, 0o700)
    writer = AuditWriter(conn, SlotChainKeys(conn, store), adir / "head.anchor", clock=lambda: NOW)
    return {
        "conn": conn,
        "store": store,
        "writer": writer,
        "anchor": adir / "head.anchor",
        "tmp": tmp_path,
    }


def test_append_is_anchored_to_the_exact_committed_head(env):
    w = env["writer"]
    with w.transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    h = head(env["conn"], COMMS)
    key = SlotChainKeys(env["conn"], env["store"]).for_epoch(1)
    a = read_anchor(COMMS_ANCHOR, env["anchor"], key)
    assert (a["chain_seq"], a["event_mac"]) == (h["chain_seq"], h["event_mac"])
    verify_chain(env["conn"], COMMS, lambda e: key)


def test_a_transaction_without_appends_does_not_touch_the_anchor(env):
    with env["writer"].transaction():
        pass
    assert not env["anchor"].exists()


def test_concurrent_writers_never_move_the_anchor_backwards(env, monkeypatch):
    seen = []
    real = anchor_mod.write_anchor

    def spy(*args, **kwargs):
        seen.append(kwargs["chain_seq"])
        return real(*args, **kwargs)

    monkeypatch.setattr(writer_mod, "write_anchor", spy)
    path = env["tmp"] / "comms.db"

    def work():
        conn = open_comms_db(path, fx.KEY)
        w = AuditWriter(conn, SlotChainKeys(conn, env["store"]), env["anchor"], clock=lambda: NOW)
        for _ in range(60):
            with w.transaction() as tx:
                tx.append("system.test_marker", payload={"count": 1})
        conn.close()

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == sorted(seen) and len(seen) == 120 and len(set(seen)) == 120
    key = SlotChainKeys(env["conn"], env["store"]).for_epoch(1)
    assert (
        read_anchor(COMMS_ANCHOR, env["anchor"], key)["chain_seq"]
        == head(env["conn"], COMMS)["chain_seq"]
        == 120
    )


def test_anchor_failure_latches_degraded_while_holding_the_lock_then_raises(env, monkeypatch):
    held = []

    def boom(*a, **k):
        held.append(writer_mod.append_guard(COMMS).locked())
        raise OSError("disk gone")

    monkeypatch.setattr(writer_mod, "write_anchor", boom)
    with pytest.raises(AnchorFailed), env["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    assert held == [True]
    assert env["conn"].execute("SELECT state, reason FROM audit_integrity").fetchone() == (
        "degraded",
        "ANCHOR_REFRESH_FAILED",
    )
    assert (
        head(env["conn"], COMMS)["chain_seq"] == 1
    )  # the event committed; only the anchor is stale
    assert not writer_mod.append_guard(COMMS).locked()


def test_unknown_kind_and_unknown_key_refused(env):
    for kind, payload in (("system.nope", {}), ("system.test_marker", {"count": 1, "extra": 2})):
        with pytest.raises(ValueError), env["writer"].transaction() as tx:
            tx.append(kind, payload=payload)
    assert head(env["conn"], COMMS) is None


def test_free_text_refused_under_every_string_field(env):
    for value in ("hello", "alice", "+61400000001", "Happy Nowruz"):
        with pytest.raises(ValueError), env["writer"].transaction() as tx:
            tx.append(
                "campaign_event",
                subject_ref="cev_" + "a" * 26,
                subject_digest="0" * 64,
                payload={"event_type": value},
            )


def test_journal_digest_changes_with_each_committed_field():
    row = {
        "event_ref": "cev_" + "a" * 26,
        "campaign_ref": "cmp_" + "b" * 26,
        "event_type": "campaign.created",
        "ts": "2026-09-24T00:00:00.000000Z",
        "payload": {"lifecycle": "DRAFT"},
    }
    base = journal_digest(row)
    for field, value in (
        ("event_ref", "cev_" + "c" * 26),
        ("campaign_ref", "cmp_" + "d" * 26),
        ("event_type", "campaign.modified"),
        ("ts", "2026-09-24T00:00:01.000000Z"),
        ("payload", {"lifecycle": "READY"}),
    ):
        assert journal_digest({**row, field: value}) != base, field


def test_journal_row_and_chain_event_commit_together(env):
    conn = env["conn"]
    fakes.plant_failure(conn, "audit_events", "INSERT")
    with pytest.raises(sqlcipher3.dbapi2.IntegrityError), env["writer"].transaction() as tx:
        append_event(conn, "campaign.created", None, {"lifecycle": "DRAFT"}, now=NOW, audit=tx)
    assert conn.execute("SELECT count(*) FROM campaign_events").fetchone()[0] == 0


def test_chain_event_binds_the_exact_journal_row(env):
    conn = env["conn"]
    with env["writer"].transaction() as tx:
        ref = append_event(
            conn, "campaign.created", None, {"lifecycle": "DRAFT"}, now=NOW, audit=tx
        )
    import json

    row = conn.execute(
        "SELECT event_ref, campaign_ref, event_type, ts, payload, event_digest FROM campaign_events"
        " WHERE event_ref = ?",
        (ref,),
    ).fetchone()
    digest = journal_digest(
        {
            "event_ref": row[0],
            "campaign_ref": row[1],
            "event_type": row[2],
            "ts": row[3],
            "payload": json.loads(row[4]),
        }
    )
    assert digest == row[5]
    assert conn.execute(
        "SELECT kind, subject_ref, subject_digest FROM audit_events"
    ).fetchone() == ("campaign_event", ref, digest)


def test_direct_chain_append_outside_audit_writer_is_forbidden():
    for path in (ROOT / "src" / "comms").rglob("*.py"):
        if path.name == "writer.py" and path.parent.name == "audit":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", getattr(node.func, "id", "")) == "append_event"
            ):
                assert not any(isinstance(a, ast.Name) and a.id == "COMMS" for a in node.args), path
