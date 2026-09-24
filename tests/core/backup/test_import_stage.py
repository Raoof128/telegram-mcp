"""comms v0.3 Task B27: export, and staged import (trust → verify → decrypt → decode → binding → diff)."""

import os

import pytest

from comms.core.backup import age
from comms.core.backup.export_import import (
    ImportRefused,
    StagedImports,
    export,
    stage_import,
)
from comms.core.installation import installation_ref
from comms.core.keys import rotate as rot
from comms.core.keys.signers import mark_signer
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW, person

PEER, OTHER = (501, 20), (502, 20)
PROVIDERS = {"telegram_user": "4242", "meta_waba": "10155"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


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
    person(source["conn"], phone="+61400000002")
    identity = age.generate_identity()
    key_file = tmp_path / "age.key"
    key_file.write_text(identity + "\n")
    key_file.chmod(0o600)
    exported = export(
        source["writer"], source["store"], age.recipient_of(identity), PROVIDERS, now=NOW
    )
    clock = Clock()
    return {
        "source": source,
        "exported": exported,
        "key_file": key_file,
        "staged": StagedImports(clock=clock),
        "clock": clock,
        "tmp": tmp_path,
    }


def _stage(env, world, **kw):
    kw.setdefault("providers", PROVIDERS)
    return stage_import(
        world["conn"],
        env["staged"],
        PEER,
        env["exported"].ciphertext,
        env["exported"].sidecar,
        env["key_file"],
        now=NOW,
        **kw,
    )


def test_export_is_signed_encrypted_and_audited(env):
    exported = env["exported"]
    assert b"+61400000001" not in exported.ciphertext
    kinds = [r[0] for r in env["source"]["conn"].execute("SELECT kind FROM audit_events")]
    assert "admin.backup_export" in kinds


def test_untrusted_signer_refused(env, tmp_path):
    fresh = _world(tmp_path, "b")
    with pytest.raises(ImportRefused, match="trust"):
        _stage(env, fresh)


def test_trust_key_authorises_this_staged_import_only(env, tmp_path):
    fresh = _world(tmp_path, "b")
    staged = _stage(env, fresh, trust_key=env["exported"].signer_key_id, adopt=True)
    assert staged.handle.startswith("cbi_")
    with pytest.raises(ImportRefused, match="trust"):
        _stage(env, fresh)  # no standing trust was created
    with pytest.raises(ImportRefused, match="trust"):
        _stage(env, fresh, trust_key="ed25519:sha256:" + "0" * 64, adopt=True)


def test_binding_mismatch_is_explicit_incompatibility(env, tmp_path):
    staged = _stage(env, env["source"], providers={**PROVIDERS, "meta_waba": "other"})
    assert "BINDING_MISMATCH" in staged.incompatibilities


def test_identity_from_argv_or_env_refused(env):
    for source in (age.generate_identity(), "env:AGE_IDENTITY", env["tmp"] / "missing.key"):
        with pytest.raises(ImportRefused, match="identity"):
            stage_import(
                env["source"]["conn"],
                env["staged"],
                PEER,
                env["exported"].ciphertext,
                env["exported"].sidecar,
                source,
                providers=PROVIDERS,
                now=NOW,
            )
    env["key_file"].chmod(0o644)
    with pytest.raises(ImportRefused, match="identity"):
        _stage(env, env["source"])


def test_diff_counts_and_base_digest_reported(env):
    staged = _stage(env, env["source"])
    assert staged.incompatibilities == ()
    assert staged.diff["recipients"] == {"added": 0, "changed": 0, "removed": 0}
    person(env["source"]["conn"], phone="+61400000003")  # the live DB moves on
    moved = _stage(env, env["source"])
    assert moved.diff["recipients"]["removed"] == 1 and moved.base_digest != staged.base_digest


def test_staging_changes_nothing(env):
    db = env["tmp"] / "a"
    before = {p.name: p.read_bytes() for p in sorted(db.glob("comms.db*"))}
    _stage(env, env["source"])
    assert {p.name: p.read_bytes() for p in sorted(db.glob("comms.db*"))} == before


def test_tps_expires_is_peer_bound_and_dies_with_restart(env):
    staged = _stage(env, env["source"])
    assert env["staged"].take(staged.handle, PEER, staged.base_digest) is staged
    again = _stage(env, env["source"])
    with pytest.raises(ImportRefused, match="peer"):
        env["staged"].take(again.handle, OTHER, again.base_digest)
    with pytest.raises(ImportRefused, match="base"):
        env["staged"].take(again.handle, PEER, "0" * 64)
    env["clock"].now += 601
    with pytest.raises(ImportRefused, match="expired"):
        env["staged"].take(again.handle, PEER, again.base_digest)
    restarted = StagedImports(clock=env["clock"])
    with pytest.raises(ImportRefused, match="unknown"):
        restarted.take(staged.handle, PEER, staged.base_digest)


def test_fresh_install_restore_requires_explicit_adopt_and_records_both_bindings(env, tmp_path):
    fresh = _world(tmp_path, "b")
    plain = _stage(env, fresh, trust_key=env["exported"].signer_key_id)
    assert "BINDING_MISMATCH" in plain.incompatibilities and plain.adopt is None
    adopted = _stage(env, fresh, trust_key=env["exported"].signer_key_id, adopt=True)
    assert adopted.incompatibilities == ()
    old, new = adopted.adopt
    assert old == env["exported"].binding and new != old


def test_a_compromised_signer_is_refused_even_when_named(env):
    source = env["source"]
    mark_signer(source["writer"], env["exported"].signer_key_id, "VERIFICATION_ONLY", now=NOW)
    with pytest.raises(ImportRefused, match="trust"):
        _stage(env, source, trust_key=env["exported"].signer_key_id)
