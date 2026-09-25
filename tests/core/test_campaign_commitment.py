"""comms v0.3 Task B8: the keyed campaign commitment on the chain (spec A12, design §B.5, D2)."""

import json
import os
from datetime import timedelta

import pytest

from comms.core.delivery import freeze
from comms.core.delivery.commitment import campaign_commitment, commit_context, verify_commitment
from comms.core.keys import rotate as rot
from tests.core import fakes
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW, person, ready

P1 = "+61400000001"
GOLDEN = "e963371530d19cc8fb76aa1e5fa88c96a509dbffa1f7a28c4ee4a38149d98c8b"


@pytest.fixture
def env(tmp_path):
    world = comms_world(tmp_path)
    rot.rotate(
        world["writer"],
        world["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    world["tx"] = {"whatsapp": fakes.FakeWhatsApp(conn=world["conn"])}
    return world


def _send(env, *, schedule=False, phone=P1):
    rcp, _ = person(env["conn"], phone=phone)
    cmp = ready(env["conn"], {"recipients": [rcp]})
    audited = commit_context(env["writer"], env["store"])
    if schedule:
        return freeze.schedule(
            env["conn"], cmp, NOW + timedelta(days=1), env["tx"], now=NOW, audited=audited
        )
    return freeze.send(env["conn"], cmp, env["tx"], now=NOW, audited=audited)


def test_commitment_is_hmac_over_generation_ref_and_snapshot_digest():
    assert campaign_commitment(bytes(range(32)), "gen_" + "a" * 26, "ab" * 32) == GOLDEN


@pytest.mark.parametrize("schedule", [False, True])
def test_chain_event_carries_commitment_not_snapshot_digest(env, schedule):
    gen = _send(env, schedule=schedule)
    snapshot, commitment, key_id = (
        env["conn"]
        .execute(
            "SELECT snapshot_digest, campaign_commitment, campaign_commit_key_id FROM generations WHERE ref = ?",
            (gen,),
        )
        .fetchone()
    )
    rows = (
        env["conn"]
        .execute("SELECT kind, payload FROM audit_events WHERE kind = 'campaign_event_committed'")
        .fetchall()
    )
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload == {
        "event_type": "campaign.scheduled" if schedule else "campaign.send_started",
        "commitment": commitment,
        "commit_key_id": key_id,
        "generation": gen,
    }
    every_payload = " ".join(r[0] for r in env["conn"].execute("SELECT payload FROM audit_events"))
    assert snapshot not in every_payload


def test_commitment_reverifies_after_body_redaction(env):
    gen = _send(env)
    # Stand-in for the B19 redaction helper: bodies go, the frozen triggers are its privilege.
    env["conn"].execute("DROP TRIGGER jobs_binding_frozen")
    env["conn"].execute("DROP TRIGGER generations_frozen")
    env["conn"].execute("UPDATE delivery_jobs SET payload = X'' WHERE payload IS NOT NULL")
    env["conn"].execute("UPDATE generations SET content = '{}' WHERE ref = ?", (gen,))
    env["conn"].commit()
    assert verify_commitment(env["conn"], env["store"], gen)


def test_a_tampered_snapshot_digest_fails(env):
    gen = _send(env)
    env["conn"].execute("DROP TRIGGER generations_frozen")
    env["conn"].execute("UPDATE generations SET snapshot_digest = ? WHERE ref = ?", ("0" * 64, gen))
    env["conn"].commit()
    assert not verify_commitment(env["conn"], env["store"], gen)


def test_rotation_keeps_old_commitments_verifiable(env):
    first = _send(env)
    rot.rotate(
        env["writer"],
        env["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    second = _send(env, phone="+61400000002")
    keys = {
        row[0]
        for row in env["conn"].execute(
            "SELECT campaign_commit_key_id FROM generations WHERE ref IN (?, ?)", (first, second)
        )
    }
    assert len(keys) == 2
    assert verify_commitment(env["conn"], env["store"], first)
    assert verify_commitment(env["conn"], env["store"], second)
    assert env["store"].versions("campaign-commit-key") == [
        1,
        2,
    ]  # kept while commitments are retained


def test_an_unaudited_freeze_reaches_no_chain(env):
    rcp, _ = person(env["conn"], phone=P1)
    cmp = ready(env["conn"], {"recipients": [rcp]})
    before = env["conn"].execute("SELECT count(*) FROM audit_events").fetchone()[0]
    gen = freeze.send(env["conn"], cmp, env["tx"], now=NOW)
    assert env["conn"].execute("SELECT count(*) FROM audit_events").fetchone()[0] == before
    assert env["conn"].execute(
        "SELECT campaign_commitment FROM generations WHERE ref = ?", (gen,)
    ).fetchone() == (None,)
