"""comms v0.3 Task B33: the Part B crash tables — every boundary converges on restart.

Rotation (each purpose: a failed proof, a crash before and after the activating
transaction), the comms.db rekey (every boundary), retention (after each phase) and backup
import (staging, and a crash before and after the commit's transaction).
"""

import os

import pytest

from comms.core.audit import cutover
from comms.core.audit.chain import COMMS, verify_chain
from comms.core.audit.integrity import is_degraded
from comms.core.audit.verify_all import _root_of, verify_all
from comms.core.backup import age
from comms.core.backup.export_import import (
    ImportCrash,
    ImportRefused,
    StagedImports,
    commit_import,
    export,
    stage_import,
)
from comms.core.installation import installation_ref
from comms.core.keys import rotate as rot
from comms.core.keys.purposes import PURPOSES
from comms.core.keys.secrets import FileSecretStore
from comms.core.keys.slots import load_active, registry_public_for
from comms.core.maintenance.retention import (
    CRASH_POINTS,
    RetentionCrash,
    RetentionPolicy,
    run_retention,
)
from comms.core.storage.db import open_comms_db
from comms.core.storage.migrations import migrate
from comms.core.storage.rekey import BOUNDARIES, KeyPointer, RekeyCrash, open_with_recovery, rekey
from comms.transports.telegram.runtime.legacy_retention import TelegramLegacyRetention
from tests.core.audit.legacy_fixtures import CHAIN_KEY, comms_world, public_for, verify_keys
from tests.core.campaign_helpers import NOW, person

ROTATED = sorted(
    n
    for n, p in PURPOSES.items()
    if p.rotation in {"new_id", "invalidate", "seal_epoch"} and p.kind in {"hmac", "ed25519"}
)


def _world(tmp_path):
    tmp_path.mkdir(mode=0o700, exist_ok=True)
    tmp_path.chmod(0o700)
    world = comms_world(tmp_path, bearer=True)
    cutover.run_cutover(world["conn"], world["port"], world["writer"], now=NOW)
    return world


def _chain_verifies(world):
    conn = world["conn"]
    verify_chain(
        conn,
        COMMS,
        world["keys"].for_epoch,
        root=_root_of(conn, COMMS),
        public_for=registry_public_for(conn),
    )


def _rotate(world, purpose, **kw):
    return rot.rotate(
        world["writer"],
        world["store"],
        purpose,
        material=os.urandom(32),
        prove=kw.pop("prove", lambda m: None),
        now=NOW,
        **kw,
    )


@pytest.mark.parametrize("purpose", ROTATED)
@pytest.mark.parametrize("boundary", ["prove_fails", "before_tx", "after_tx"])
def test_rotation_converges_after_every_boundary(tmp_path, purpose, boundary):
    world = _world(tmp_path)
    if boundary == "prove_fails":

        def refuse(material):
            raise ValueError("proof failed")

        with pytest.raises(ValueError):
            _rotate(world, purpose, prove=refuse)
    else:
        with pytest.raises(rot.RotationCrash):
            _rotate(world, purpose, crash_at=boundary)
    _rotate(world, purpose)  # the restart: the next rotation
    load_active(world["conn"], world["store"], purpose)
    assert rot.find_orphans(world["conn"], world["store"]) == {}
    _chain_verifies(world)


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_rekey_converges_after_every_boundary(tmp_path, boundary):
    secrets = FileSecretStore(tmp_path / "secrets")
    pointer = KeyPointer(tmp_path / "secrets" / "comms-db-key.pointer")
    key = os.urandom(32)
    secrets.put("comms-db-key", 1, key)
    pointer.set(1)
    conn = open_comms_db(tmp_path / "comms.db", key)
    migrate(conn)
    with pytest.raises(RekeyCrash):
        rekey(conn, tmp_path / "comms.db", secrets, pointer, crash_at=boundary)
    conn = open_with_recovery(tmp_path / "comms.db", secrets, pointer)
    rekey(conn, tmp_path / "comms.db", secrets, pointer)
    assert len(secrets.versions("comms-db-key")) == 1
    open_with_recovery(tmp_path / "comms.db", secrets, pointer).close()


KEEP = RetentionPolicy(3650, 3650, 3650, 3650, 3650, 3650)


@pytest.mark.parametrize("boundary", CRASH_POINTS)
def test_retention_converges_after_every_phase(tmp_path, boundary):
    world = _world(tmp_path)
    legacy = TelegramLegacyRetention(world["port"].conn, CHAIN_KEY, public_for)
    run = lambda **kw: run_retention(
        world["conn"], legacy, KEEP, world["writer"], now=NOW, store=world["store"], **kw
    )
    with pytest.raises(RetentionCrash):
        run(crash_at=boundary)
    report = run()
    assert report.outcome in ("ok", "blocked") and not is_degraded(world["conn"])
    verified = verify_all(world["conn"], world["port"].conn, verify_keys(world))
    assert verified.problems == ()


PEER = (501, 20)
PROVIDERS = {"telegram_user": "4242"}


@pytest.fixture
def backup(tmp_path):
    source = _world(tmp_path / "a")
    for purpose in ("backup-key",):
        _rotate(source, purpose)
    installation_ref(source["conn"], now=NOW)
    person(source["conn"], phone="+61400000001")
    identity = age.generate_identity()
    key_file = tmp_path / "age.key"
    key_file.write_text(identity + "\n")
    key_file.chmod(0o600)
    exported = export(
        source["writer"], source["store"], age.recipient_of(identity), PROVIDERS, now=NOW
    )
    target = _world(tmp_path / "b")
    installation_ref(target["conn"], now=NOW)
    return exported, key_file, target


def _stage(target, staged, exported, key_file):
    return stage_import(
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


def test_import_staging_is_lost_on_restart_and_changes_nothing(backup):
    exported, key_file, target = backup
    stage = _stage(target, StagedImports(), exported, key_file)
    with pytest.raises(ImportRefused, match="unknown"):
        commit_import(
            target["writer"], target["store"], StagedImports(), stage.handle, PEER, now=NOW
        )
    staged = StagedImports()
    stage = _stage(target, staged, exported, key_file)
    commit_import(target["writer"], target["store"], staged, stage.handle, PEER, now=NOW)
    _chain_verifies(target)


@pytest.mark.parametrize("boundary", ["before_tx", "after_tx"])
def test_import_commit_converges_after_every_boundary(backup, boundary):
    exported, key_file, target = backup
    staged = StagedImports()
    stage = _stage(target, staged, exported, key_file)
    with pytest.raises(ImportCrash):
        commit_import(
            target["writer"],
            target["store"],
            staged,
            stage.handle,
            PEER,
            now=NOW,
            crash_at=boundary,
        )
    if boundary == "before_tx":
        commit_import(target["writer"], target["store"], staged, stage.handle, PEER, now=NOW)
    else:
        with pytest.raises(ImportRefused, match="base"):  # already applied, exactly once
            commit_import(target["writer"], target["store"], staged, stage.handle, PEER, now=NOW)
    kinds = [
        r[0]
        for r in target["conn"].execute(
            "SELECT kind FROM audit_events WHERE kind = 'admin.backup_import'"
        )
    ]
    assert kinds == ["admin.backup_import"]
    assert rot.find_orphans(target["conn"], target["store"]) == {}
    _chain_verifies(target)
