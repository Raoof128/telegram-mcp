"""D39-PRE Task E2: ``open_comms_state`` fails closed; bootstrap states (owner amendment)."""

import os

import pytest

from comms.core.audit.cutover import run_cutover
from comms.core.audit.integrity import is_degraded
from comms.core.keys import rotate as rot
from comms.runtime.paths import CommsPaths
from comms.runtime.provision import provision
from comms.runtime.state import StateRefused, bootstrap_state, open_comms_state
from tests.core.audit.legacy_fixtures import legacy_port
from tests.runtime.test_provision import NOW


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run").mkdir(mode=0o700)
    p = CommsPaths(tmp_path / "state")
    provision(p, now=NOW, runtime_dir=tmp_path / "run")
    return p


def _open(paths):
    return open_comms_state(paths, clock=lambda: NOW)


def _genesis(paths, tmp_path):
    state = _open(paths)
    run_cutover(state.conn, legacy_port(tmp_path), state.writer, now=NOW)
    state.conn.close()


def test_opens_a_provisioned_state(paths):
    state = _open(paths)
    assert bootstrap_state(state.conn, state.store, paths) == "PROVISIONED"
    assert not is_degraded(state.conn)
    assert repr(state) == "CommsState(<redacted>)"


def test_unprovisioned_is_refused(tmp_path):
    with pytest.raises(StateRefused, match="not provisioned"):
        _open(CommsPaths(tmp_path / "nothing"))


def test_a_key_that_does_not_open_the_database_is_refused(paths):
    (paths.secrets_dir / "comms-db-key").rename(paths.secrets_dir / "comms-db-key.moved")
    (paths.secrets_dir / "comms-db-key").mkdir(mode=0o700)
    with pytest.raises(StateRefused, match="does not open"):
        _open(paths)


def test_a_migration_failure_is_refused(paths, monkeypatch):
    def broken(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr("comms.runtime.state.migrate", broken)
    with pytest.raises(StateRefused, match="could not be migrated"):
        _open(paths)


def test_an_integrity_failure_is_refused(paths, monkeypatch):
    monkeypatch.setattr("comms.runtime.state._checks_pass", lambda conn: False)
    with pytest.raises(StateRefused, match="integrity check"):
        _open(paths)


def test_a_corrupt_required_key_is_refused(paths):
    victim = min((paths.slots_dir / "audit-checkpoint-key").iterdir())
    victim.chmod(0o600)
    victim.write_bytes(b"\0" * 32)
    with pytest.raises(StateRefused, match="required comms key"):
        _open(paths)


def test_fresh_provision_without_chain_or_anchor_is_not_degraded(paths):
    assert not paths.anchor.exists()
    state = _open(paths)
    assert bootstrap_state(state.conn, state.store, paths) == "PROVISIONED"
    assert not is_degraded(state.conn)


def test_after_the_genesis_the_state_is_ready(paths, tmp_path):
    _genesis(paths, tmp_path)
    state = _open(paths)
    assert bootstrap_state(state.conn, state.store, paths) == "READY"
    assert not is_degraded(state.conn)


def test_a_missing_anchor_after_the_chain_exists_starts_degraded(paths, tmp_path):
    _genesis(paths, tmp_path)
    paths.anchor.unlink()
    state = _open(paths)
    assert bootstrap_state(state.conn, state.store, paths) == "CHAIN_INITIALIZED"
    assert is_degraded(state.conn)


def test_an_anchor_one_event_behind_is_re_anchored(paths, tmp_path):
    _genesis(paths, tmp_path)
    stale = paths.anchor.read_bytes()
    state = _open(paths)
    rot.rotate(state.writer, state.store, "cursor-key", material=os.urandom(32),
               prove=lambda m: None, now=NOW)  # one audited event past the stale anchor  # fmt: skip
    state.conn.close()
    paths.anchor.write_bytes(stale)  # the crash between the commit and its refresh
    state = _open(paths)
    assert not is_degraded(state.conn)
    assert bootstrap_state(state.conn, state.store, paths) == "READY"
