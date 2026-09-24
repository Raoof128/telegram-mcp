"""comms v0.3 Task B20: retention of directory identities and retired key material; the event."""

import json
import os
from datetime import timedelta

import pytest

from comms.core.audit import cutover
from comms.core.audit.chain import COMMS, insert_checkpoint
from comms.core.campaigns import directory as d
from comms.core.delivery import freeze
from comms.core.delivery.commitment import commit_context
from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active
from comms.core.maintenance.retention import (
    RetentionPolicy,
    purge_retired_keys,
    redact_identities,
    run_retention,
)
from comms.core.storage.db import write_tx
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.core import fakes
from tests.core.audit.legacy_fixtures import CHAIN_KEY, comms_world, public_for
from tests.core.campaign_helpers import NOW, person, ready

OLD = NOW - timedelta(days=100)
KEEP = {
    "exposure_ledger_days": 3650,
    "receipt_days": 3650,
    "message_ref_days": 3650,
    "audit_events_days": 3650,
    "campaign_body_days": 3650,
    "identity_retention_days": 30,
}


@pytest.fixture
def world(tmp_path):
    return comms_world(tmp_path)


def _identities(conn, cutoff=NOW - timedelta(days=30)):
    with write_tx(conn):
        return redact_identities(conn, cutoff=cutoff)


def test_identities_of_long_disabled_endpoints_redacted(world):
    conn = world["conn"]
    _rcp, pts = person(conn, phone="+61400000001")
    d.set_enabled(conn, pts["wa"], False, now=OLD)
    identity_id = conn.execute(
        "SELECT identity_id FROM contact_points WHERE ref = ?", (pts["wa"],)
    ).fetchone()[0]
    assert _identities(conn) == 1
    marker = f"redacted:{identity_id}"
    assert (
        conn.execute(
            "SELECT identity FROM delivery_identities WHERE id = ?", (identity_id,)
        ).fetchone()[0]
        == marker
    )
    assert (
        conn.execute(
            "SELECT platform_identity FROM contact_points WHERE ref = ?", (pts["wa"],)
        ).fetchone()[0]
        == marker
    )
    every = " ".join(
        str(v)
        for t in ("delivery_identities", "contact_points")
        for r in conn.execute(f"SELECT * FROM {t}")
        for v in r
    )
    assert "400000001" not in every
    assert _identities(conn) == 0  # once only


def test_recently_disabled_or_enabled_endpoints_keep_their_identity(world):
    conn = world["conn"]
    _rcp, recent = person(conn, phone="+61400000002")
    d.set_enabled(conn, recent["wa"], False, now=NOW - timedelta(days=5))
    person(conn, phone="+61400000003")
    assert _identities(conn) == 0


def test_identity_referenced_by_unresolved_job_kept(world):
    conn = world["conn"]
    rcp, pts = person(conn, phone="+61400000004")
    cmp = ready(conn, {"recipients": [rcp]})
    freeze.send(
        conn, cmp, {"whatsapp": fakes.FakeWhatsApp(conn=conn)}, now=NOW
    )  # PENDING, unresolved
    d.set_enabled(conn, pts["wa"], False, now=OLD)
    assert _identities(conn) == 0


def _purge(world):
    return purge_retired_keys(world["conn"], world["store"])


def test_unreferenced_retired_public_keys_removed(world):
    for _ in range(2):
        rot.rotate(
            world["writer"],
            world["store"],
            "audit-checkpoint-key",
            material=os.urandom(32),
            prove=lambda m: None,
            now=NOW,
        )
    counts = _purge(world)
    assert counts["public_keys"] == 2
    left = (
        world["conn"]
        .execute("SELECT trust_state FROM verification_keys WHERE purpose = 'audit-checkpoint-key'")
        .fetchall()
    )
    assert left == [("ACTIVE",)]


def test_key_referenced_by_retained_checkpoint_kept(world):
    key = load_active(world["conn"], world["store"], "audit-checkpoint-key")[0]
    with world["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    with write_tx(world["conn"]):
        insert_checkpoint(
            world["conn"], COMMS, key, now="2026-09-01T00:00:00.000000Z", reason="PERIODIC"
        )
    rot.rotate(
        world["writer"],
        world["store"],
        "audit-checkpoint-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    assert _purge(world)["public_keys"] == 0
    assert (
        world["conn"]
        .execute("SELECT count(*) FROM verification_keys WHERE purpose = 'audit-checkpoint-key'")
        .fetchone()[0]
        == 2
    )


def test_retired_audit_chain_secret_destroyed_once_its_epoch_is_truncated(world):
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    rot.rotate(
        world["writer"],
        world["store"],
        "audit-chain-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    assert _purge(world)["secrets"] == 0  # epoch 1 is still retained
    assert world["store"].versions("audit-chain-key") == [1, 2]
    key = load_active(world["conn"], world["store"], "audit-checkpoint-key")[0]
    with world["writer"].transaction() as tx:
        tx.append("system.test_marker", payload={"count": 1})
    with write_tx(world["conn"]):
        insert_checkpoint(
            world["conn"], COMMS, key, now="2026-09-05T00:00:00.000000Z", reason="PERIODIC"
        )
    legacy = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    report = run_retention(
        world["conn"],
        legacy,
        RetentionPolicy(**{**KEEP, "audit_events_days": 10}),
        world["writer"],
        now=NOW,
        store=world["store"],
    )
    assert report.phases["comms_chain"] > 0 and report.phases["secrets"] == 1
    assert world["store"].versions("audit-chain-key") == [2]
    assert (
        world["conn"]
        .execute("SELECT state FROM key_slots WHERE purpose = 'audit-chain-key' AND version = 1")
        .fetchone()[0]
        == "DESTROYED"
    )


def test_retired_campaign_commit_secret_kept_while_a_commitment_under_it_is_retained(world):
    conn = world["conn"]
    rot.rotate(
        world["writer"],
        world["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    rcp, _ = person(conn, phone="+61400000005")
    cmp = ready(conn, {"recipients": [rcp]})
    freeze.send(
        conn,
        cmp,
        {"whatsapp": fakes.FakeWhatsApp(conn=conn)},
        now=NOW,
        audited=commit_context(world["writer"], world["store"]),
    )
    rot.rotate(
        world["writer"],
        world["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    assert _purge(world)["secrets"] == 0
    assert world["store"].versions("campaign-commit-key") == [1, 2]


def test_maintenance_event_appended_with_counts_and_root(world):
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    legacy = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    report = run_retention(
        world["conn"],
        legacy,
        RetentionPolicy(**KEEP),
        world["writer"],
        now=NOW,
        store=world["store"],
    )
    (payload,) = [
        json.loads(r[0])
        for r in world["conn"].execute(
            "SELECT payload FROM audit_events WHERE kind = 'maintenance.retention_purge'"
        )
    ]
    assert payload == {
        **report.phases,
        "comms_root": report.roots["comms"],
        "legacy_root": report.roots["legacy"],
    }
    assert set(report.phases) >= {"identities", "public_keys", "secrets"}
