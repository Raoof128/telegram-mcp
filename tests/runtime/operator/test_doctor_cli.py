"""D39-PRE Task E9: ``comms doctor`` over the real state, read-only, and what it reports."""

import hashlib
import json
from pathlib import Path

import pytest

from comms.cli import main
from comms.core.credentials import record_confirmed, rotate_credential
from comms.runtime.state import open_comms_state
from tests.runtime.operator.conftest import _now


def _doctor(paths, capsys, *extra):
    main(["doctor", "--state-dir", str(paths.state_dir), *extra])
    return json.loads(capsys.readouterr().out)


def _codes(report):
    return {(f["code"], f["subject"]) for f in report["findings"]}


def _digest(paths):
    return hashlib.sha256(paths.db.read_bytes()).hexdigest()


def test_a_fresh_install_reports_the_pending_cutover_and_missing_credentials(daemon_world, capsys):
    paths = daemon_world["paths"]
    daemon_world["state"].conn.close()
    before = _digest(paths)
    report = _doctor(paths, capsys)
    assert report["bootstrap"] == "PROVISIONED"
    codes = {code for code, _subject in _codes(report)}
    # a fresh state: no cutover yet, no credentials, and retention has never run (the daemon's
    # maintenance loop runs it daily once the cutover releases the write hold)
    assert codes == {"CUTOVER_INCOMPLETE", "CREDENTIAL_NOT_CONFIGURED", "MAINTENANCE_OVERDUE"}
    assert report["ok"] is False
    assert _digest(paths) == before  # the doctor never writes


def test_after_the_cutover_and_retention_only_missing_credentials_remain(daemon_world, capsys):
    daemon_world["run"]("cutover", "run")
    daemon_world["run"]("retention", "run")
    daemon_world["state"].conn.close()
    report = _doctor(daemon_world["paths"], capsys)
    assert report["bootstrap"] == "READY"
    assert {code for code, _s in _codes(report)} == {"CREDENTIAL_NOT_CONFIGURED"}
    assert report["ok"] is True  # missing credentials are the owner's choice of providers


def test_an_unconfirmed_webhook_secret_is_reported_until_meta_confirms_it(daemon_world, capsys):
    run, paths = daemon_world["run"], daemon_world["paths"]
    run("cutover", "run")
    state = daemon_world["state"]
    version = rotate_credential(state.writer, state.secrets, "meta-app-secret", b"0" * 32,
                                prove=lambda v: None, now=_now())  # fmt: skip
    state.conn.close()
    assert ("CREDENTIAL_UNCONFIRMED", "meta-app-secret") in _codes(_doctor(paths, capsys))
    again = open_comms_state(paths, clock=_now)
    record_confirmed(again.conn, "meta-app-secret", version)
    again.conn.close()
    assert ("CREDENTIAL_UNCONFIRMED", "meta-app-secret") not in _codes(_doctor(paths, capsys))


def test_production_mode_exits_nonzero_unless_ok(daemon_world, capsys):
    daemon_world["state"].conn.close()
    with pytest.raises(SystemExit) as failed:
        _doctor(daemon_world["paths"], capsys, "--production")
    assert failed.value.code != 0


def test_an_unprovisioned_state_is_one_finding(tmp_path, capsys):
    main(["doctor", "--state-dir", str(tmp_path / "nothing")])
    report = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in report["findings"]] == ["NOT_PROVISIONED"]
    assert not Path(tmp_path / "nothing").exists()  # never created
