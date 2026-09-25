"""comms v0.3 Task B28: import commit — base recheck, replace, epoch seal."""

import os

import pytest

from comms.core.audit.chain import COMMS, head, verify_chain
from comms.core.audit.integrity import AuditIntegrityDegraded, latch_degraded
from comms.core.backup import age
from comms.core.backup.export_import import (
    ImportRefused,
    StagedImports,
    commit_import,
    export,
    stage_import,
)
from comms.core.backup.payload import _directory
from comms.core.campaigns import directory as d
from comms.core.installation import installation_ref
from comms.core.keys import rotate as rot
from comms.core.keys.slots import registry_public_for
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW, person

PEER = (501, 20)
PROVIDERS = {"telegram_user": "4242", "meta_waba": "10155"}


def _world(tmp_path, name):
    (tmp_path / name).mkdir(mode=0o700, exist_ok=True)
    (tmp_path / name).chmod(0o700)
    w = comms_world(tmp_path / name)
    rot.rotate(
        w["writer"],
        w["store"],
        "backup-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    installation_ref(w["conn"], now=NOW)
    return w


@pytest.fixture
def env(tmp_path):
    source = _world(tmp_path, "a")
    person(source["conn"], phone="+61400000001")
    _rcp, pts = person(source["conn"], phone="+61400000002")
    d.set_enabled(source["conn"], pts["wa"], False, now=NOW)
    named = d.add_recipient(source["conn"], now=NOW, display_name="Sara Karimi")  # D7's label
    identity = age.generate_identity()
    key_file = tmp_path / "age.key"
    key_file.write_text(identity + "\n")
    key_file.chmod(0o600)
    exported = export(
        source["writer"], source["store"], age.recipient_of(identity), PROVIDERS, now=NOW
    )
    target = _world(tmp_path, "b")
    person(target["conn"], phone="+61400000009")  # a local-only recipient the backup lacks
    staged = StagedImports()
    stage = stage_import(
        target["conn"],
        staged,
        PEER,
        exported.ciphertext,
        exported.sidecar,
        key_file,
        providers=PROVIDERS,
        trust_key=exported.signer_key_id,
        adopt=True,
        now=NOW,
    )
    return {"source": source, "target": target, "staged": staged, "stage": stage, "named": named}


def _commit(env, **kw):
    t = env["target"]
    return commit_import(
        t["writer"], t["store"], env["staged"], env["stage"].handle, PEER, now=NOW, **kw
    )


def test_stale_base_refuses(env):
    person(env["target"]["conn"], phone="+61400000010")  # the live directory moved after staging
    with pytest.raises(ImportRefused, match="base"):
        _commit(env)


def test_missing_objects_disabled_not_deleted(env):
    conn = env["target"]["conn"]
    before = conn.execute("SELECT count(*) FROM contact_points").fetchone()[0]
    _commit(env)
    local = conn.execute(
        "SELECT c.enabled FROM contact_points c JOIN delivery_identities i ON i.id = c.identity_id"
        " WHERE i.identity = '+61400000009'"
    ).fetchone()
    assert local == (0,)
    assert conn.execute("SELECT count(*) FROM contact_points").fetchone()[0] == before + 2


def test_import_opens_a_new_epoch_first_event_backup_import(env):
    t = env["target"]
    before = head(t["conn"], COMMS)["chain_epoch"]
    _commit(env)
    rows = (
        t["conn"]
        .execute(
            "SELECT chain_epoch, chain_seq, kind FROM audit_events WHERE chain_epoch = ? ORDER BY chain_seq",
            (before + 1,),
        )
        .fetchall()
    )
    assert rows[0] == (before + 1, 1, "admin.backup_import")
    assert rows[1][2] == "admin.backup_adopt"  # the explicit rebinding, both digests recorded
    verify_chain(t["conn"], COMMS, t["keys"].for_epoch, public_for=registry_public_for(t["conn"]))


def test_import_refused_while_degraded(env):
    latch_degraded(env["target"]["conn"], reason="ANCHOR_REFRESH_FAILED", now=NOW)
    with pytest.raises(AuditIntegrityDegraded):
        _commit(env)


def test_round_trip_export_import_equal_directory(env):
    _commit(env)
    source, target = _directory(env["source"]["conn"]), _directory(env["target"]["conn"])
    for section in source:
        imported = [row for row in target[section] if row in source[section]]
        assert imported == source[section], section


def test_a_recipients_display_name_survives_the_round_trip(env):
    """D39-PRE E10c found: the backup predated D7's display_name, so a restore dropped names."""
    _commit(env)
    row = (
        env["target"]["conn"]
        .execute("SELECT display_name FROM recipients WHERE ref = ?", (env["named"],))
        .fetchone()
    )
    assert row == ("Sara Karimi",)
