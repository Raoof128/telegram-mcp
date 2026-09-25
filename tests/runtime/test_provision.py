"""D39-PRE Task E1: ``comms keys provision`` (R-E4) — encrypted, fail-closed, idempotent."""

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from comms.core.audit.chain import COMMS, head
from comms.core.keys.secrets import FileSecretStore
from comms.core.keys.slots import KeySlotStore, load_active
from comms.core.storage.rekey import KeyPointer, open_with_recovery
from comms.runtime.paths import CommsPaths
from comms.runtime.provision import PROVISIONED_KEYS, ProvisionRefused, provision
from comms.transports.telegram.runtime.lock import acquire_lock

NOW = datetime(2026, 9, 25, tzinfo=UTC)


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # AF_UNIX-length paths stay short
    Path("run").mkdir(mode=0o700)
    return Path("run")


def _open(paths):
    return open_with_recovery(
        paths.db, FileSecretStore(paths.secrets_dir), KeyPointer(paths.db_key_pointer)
    )


def test_first_provision_creates_an_encrypted_db_and_every_key(tmp_path, run):
    paths = CommsPaths(tmp_path / "state")
    report = provision(paths, now=NOW, runtime_dir=run)
    assert paths.db.read_bytes()[:16] != b"SQLite format 3\x00"
    assert set(report.created) == {"comms-db-key", *PROVISIONED_KEYS}
    for directory in (paths.state_dir, paths.root, paths.secrets_dir, paths.slots_dir,
                      paths.anchor_dir):  # fmt: skip
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.db_key_pointer.stat().st_mode) == 0o600
    conn = _open(paths)
    store = KeySlotStore(paths.slots_dir)
    for purpose in PROVISIONED_KEYS:
        load_active(conn, store, purpose)  # material loads, key id recomputes
    assert conn.execute("SELECT count(*) FROM installation").fetchone()[0] == 1
    assert head(conn, COMMS) is None  # no event before the genesis: the cutover can still run
    assert {"audit-chain-key", "audit-checkpoint-key", "privacy-key"} <= set(report.legacy_created)
    assert stat.S_IMODE(paths.legacy_keys.stat().st_mode) == 0o700


def test_provision_is_idempotent(tmp_path, run):
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=run)
    files = {p: p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
    again = provision(paths, now=NOW, runtime_dir=run)
    assert again.created == () and again.legacy_created == ()
    after = {p: p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
    assert set(after) == set(files)
    assert all(after[p] == files[p] for p in files if p.parent != paths.root)  # key material


def test_provision_refuses_while_a_daemon_holds_the_lock(tmp_path, run):
    held = acquire_lock(run / "runtime.lock", mode="daemon")
    try:
        with pytest.raises(ProvisionRefused, match="a daemon is running"):
            provision(CommsPaths(tmp_path / "state"), now=NOW, runtime_dir=run)
    finally:
        held.release()
    assert not (tmp_path / "state").exists()  # refused before touching anything


def test_damaged_existing_material_is_refused_not_replaced(tmp_path, run):
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=run)
    victim = min(p for p in paths.slots_dir.rglob("*") if p.is_file())
    victim.chmod(0o600)
    victim.write_bytes(b"\0" * 32)
    with pytest.raises(ProvisionRefused, match="damaged"):
        provision(paths, now=NOW, runtime_dir=run)
    assert victim.read_bytes() == b"\0" * 32  # untouched


def test_a_db_key_without_its_database_is_refused(tmp_path, run):
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=run)
    paths.db.unlink()
    with pytest.raises(ProvisionRefused, match="damaged"):
        provision(paths, now=NOW, runtime_dir=run)
    assert not paths.db.exists()


def test_a_wide_comms_directory_is_refused(tmp_path, run):
    paths = CommsPaths(tmp_path / "state")
    paths.root.mkdir(parents=True)
    paths.root.chmod(0o755)
    with pytest.raises(ProvisionRefused, match="0700"):
        provision(paths, now=NOW, runtime_dir=run)


def test_the_report_names_purposes_only(tmp_path, run):
    report = provision(CommsPaths(tmp_path / "state"), now=NOW, runtime_dir=run)
    assert all(name.replace("-", "").isalnum() and len(name) < 40 for name in report.created)


def test_after_the_genesis_a_missing_purpose_is_minted_by_the_audited_rotation(tmp_path, run):
    """An install whose chain began before a purpose existed: provision adds it, audited."""
    from comms.core.audit import cutover
    from comms.core.audit.writer import AuditWriter, SlotChainKeys
    from comms.core.keys.slots import bootstrap_comms_audit_keys
    from comms.core.storage.db import open_comms_db
    from comms.core.storage.migrations import migrate
    from tests.core.audit.legacy_fixtures import legacy_port

    paths = CommsPaths(tmp_path / "state")
    for d in (paths.state_dir, paths.root, paths.secrets_dir, paths.slots_dir, paths.anchor_dir):
        d.mkdir(mode=0o700)
    secrets, store = FileSecretStore(paths.secrets_dir), KeySlotStore(paths.slots_dir)
    secrets.put("comms-db-key", 1, b"k" * 32)
    KeyPointer(paths.db_key_pointer).set(1)
    conn = open_comms_db(paths.db, b"k" * 32)
    migrate(conn)
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    writer = AuditWriter(conn, SlotChainKeys(conn, store), paths.anchor, clock=lambda: NOW)
    cutover.run_cutover(conn, legacy_port(tmp_path), writer, now=NOW)
    conn.close()

    report = provision(paths, now=NOW, runtime_dir=run)
    assert set(report.created) == set(PROVISIONED_KEYS) - {
        "audit-chain-key",
        "audit-checkpoint-key",
    }
    conn = _open(paths)
    rotations = conn.execute(
        "SELECT count(*) FROM audit_events WHERE kind = 'admin.key_rotation'"
    ).fetchone()[0]
    assert rotations == len(report.created)


def test_the_cli_runs_provision_locally_without_a_daemon(tmp_path, run, capsys):
    import json

    from comms.cli import main

    main(["keys", "provision", "--state-dir", str(tmp_path / "state"), "--runtime-dir", str(run)])
    printed = json.loads(capsys.readouterr().out)
    assert set(printed["provisioned"]) == {"comms-db-key", *PROVISIONED_KEYS}
    main(["keys", "provision", "--state-dir", str(tmp_path / "state"), "--runtime-dir", str(run)])
    assert json.loads(capsys.readouterr().out)["provisioned"] == []


def test_the_default_state_dir_is_the_installers(monkeypatch):
    from comms.runtime.paths import default_state_dir

    monkeypatch.delenv("TELEGRAM_MCP_STATE_DIR", raising=False)
    assert default_state_dir() == Path("/var/db/telegram-mcp")
    monkeypatch.setenv("TELEGRAM_MCP_STATE_DIR", "/elsewhere")
    assert default_state_dir() == Path("/elsewhere")


def test_a_fresh_install_gets_an_empty_legacy_chain_anchored_so_the_cutover_can_seal_it(
    tmp_path, run
):
    """R-E9: with no legacy history, an authenticated anchor at sequence 0 names the empty
    legacy chain (the core rule reads that as CLEAN); without it the cutover can never run."""
    from comms.transports.telegram.disclosure.audit.anchor import derive_integrity
    from comms.transports.telegram.keys.store import load_key, set_store_dir
    from comms.transports.telegram.storage.db import open_db

    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=run)
    assert stat.S_IMODE(paths.legacy_anchor.stat().st_mode) == 0o600
    set_store_dir(paths.legacy_keys)
    legacy = open_db(paths.legacy_db)
    assert derive_integrity(legacy, load_key("audit-chain-key"), paths.legacy_anchor) == "CLEAN"


def test_an_existing_legacy_database_is_never_touched(tmp_path, run):
    paths = CommsPaths(tmp_path / "state")
    paths.state_dir.mkdir(mode=0o700)
    paths.legacy_db.write_bytes(b"an existing legacy database")
    provision(paths, now=NOW, runtime_dir=run)
    assert paths.legacy_db.read_bytes() == b"an existing legacy database"
    assert not paths.legacy_anchor.exists()


def test_the_database_key_rotates_locally_and_the_new_key_opens_the_database(tmp_path, run, capsys):
    import json

    from comms.cli import main

    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=run)
    before = KeyPointer(paths.db_key_pointer).get()
    main(
        [
            "keys",
            "rotate",
            "comms-db-key",
            "--state-dir",
            str(paths.state_dir),
            "--runtime-dir",
            str(run),
        ]
    )
    assert json.loads(capsys.readouterr().out) == {"purpose": "comms-db-key", "version": before + 1}
    assert KeyPointer(paths.db_key_pointer).get() == before + 1
    assert FileSecretStore(paths.secrets_dir).versions("comms-db-key") == [before + 1]
    _open(paths).close()


def test_the_database_key_is_not_rotated_while_a_daemon_runs(tmp_path, run):
    from comms.cli import main

    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=run)
    held = acquire_lock(run / "runtime.lock", mode="daemon")
    try:
        with pytest.raises(SystemExit):
            main(["keys", "rotate", "comms-db-key", "--state-dir", str(paths.state_dir),
                  "--runtime-dir", str(run)])  # fmt: skip
    finally:
        held.release()
    assert KeyPointer(paths.db_key_pointer).get() == 1
