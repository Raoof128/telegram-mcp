"""Task 8: doctor reports honestly, and --production fails in this phase."""

import os
import stat

import pytest

from telegram_mcp.doctor import (
    CHECKS,
    PRODUCTION_REQUIRED,
    DoctorContext,
    doctor,
)
from telegram_mcp.ipc.tunnel import add_pin, spki_digest
from telegram_mcp.keys.store import provision_missing
from telegram_mcp.storage.db import open_db


def _status(report, name):
    return next(check["status"] for check in report["checks"] if check["name"] == name)


def test_headless_defaults_run_and_skip_what_cannot_be_checked():
    report = doctor()
    names = [check["name"] for check in report["checks"]]
    assert names == [name for name in CHECKS if name != "off.probe"]
    assert _status(report, "runtime.python") == "ok"
    assert _status(report, "runtime.mcp_sdk") == "ok"
    assert _status(report, "consent.selftest") == "ok"
    for deferred in ("telegram.auth_state", "mcp.endpoint_credentials", "tunnel.tls_trust"):
        assert _status(report, deferred) == "skipped"
    assert _status(report, "service_accounts.separation") == "skipped"
    assert report["status"] == "ok"


def test_production_gate_fails_while_phase_four_checks_cannot_run():
    report = doctor(production=True)
    assert report["status"] == "fail"
    unmet = set(report["unmet_production_checks"])
    assert {"telegram.auth_state", "mcp.endpoint_credentials", "tunnel.tls_trust"} <= unmet
    assert unmet <= PRODUCTION_REQUIRED


def test_keys_and_database_checks_pass_against_a_real_store(tmp_path):
    store = tmp_path / "keys"
    provision_missing(store)
    conn = open_db(tmp_path / "db" / "meta.db")
    add_pin(store, spki_digest(b"tunnel-client-der"), now=1_000)
    report = doctor(
        ("keys.inventory", "db.integrity", "tunnel.pin"),
        context=DoctorContext(store_dir=store, conn=conn),
    )
    assert _status(report, "keys.inventory") == "ok"
    assert _status(report, "db.integrity") == "ok"
    assert _status(report, "tunnel.pin") == "ok"
    assert report["status"] == "ok"


def test_unpinned_tunnel_warns_rather_than_failing(tmp_path):
    store = tmp_path / "keys"
    provision_missing(store)
    report = doctor(("tunnel.pin",), context=DoctorContext(store_dir=store))
    assert _status(report, "tunnel.pin") == "warn"
    assert report["status"] == "ok"


def test_corrupt_database_is_reported_as_a_failure(tmp_path):
    target = tmp_path / "db" / "meta.db"
    conn = open_db(target)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    raw = bytearray(target.read_bytes())
    for offset in range(100, min(len(raw), 6000)):
        raw[offset] ^= 0xFF
    target.write_bytes(bytes(raw))
    report = doctor(("db.integrity",), context=DoctorContext(db_path=target))
    assert _status(report, "db.integrity") == "fail"
    assert report["status"] == "fail"


def test_loose_socket_directory_is_reported_as_a_failure(tmp_path):
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir(mode=0o755)
    os.chmod(runtime_dir, 0o755)
    report = doctor(("sockets.permissions",), context=DoctorContext(runtime_dir=runtime_dir))
    assert _status(report, "sockets.permissions") == "fail"


def test_socket_permission_check_accepts_the_installed_layout(tmp_path):
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir(mode=0o770)
    os.chmod(runtime_dir, 0o770)
    for name in ("admin.sock", "consent.sock"):
        path = runtime_dir / name
        path.write_bytes(b"")
        os.chmod(path, 0o660)
    report = doctor(("sockets.permissions",), context=DoctorContext(runtime_dir=runtime_dir))
    assert _status(report, "sockets.permissions") == "ok"
    assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o770


def test_off_probe_passes_when_nothing_is_running(tmp_path):
    report = doctor(context=DoctorContext(runtime_dir=tmp_path / "absent"), off=True)
    assert _status(report, "off.probe") == "ok"
    assert report["off"] is True


def test_off_probe_fails_while_a_port_is_open(tmp_path):
    import socket as socket_module

    listener = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    listener.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        report = doctor(("off.probe",), context=DoctorContext(ports=(port,)), off=True)
    finally:
        listener.close()
    assert _status(report, "off.probe") == "fail"
    assert report["status"] == "fail"


def test_unknown_check_name_is_refused():
    with pytest.raises(ValueError):
        doctor(("does.not.exist",))


def test_off_probe_ignores_processes_that_merely_mention_the_labels():
    """A probe like `dscl . -read /Users/telegram-mcpd` is not a runtime."""
    import telegram_mcp.doctor as doctor_module
    from telegram_mcp.runtime.bootstrap import ProcessEntry

    mentions = [
        ProcessEntry(
            pid=4242, uid=os.getuid(), argv=("/usr/bin/dscl", ".", "-read", "/Users/telegram-mcpd")
        ),
        ProcessEntry(
            pid=4243,
            uid=os.getuid(),
            argv=("/usr/bin/sudo", "-n", "-u", "telegram-mcp-tunnel", "true"),
        ),
    ]
    original = doctor_module._list_processes_ps
    doctor_module._list_processes_ps = lambda: mentions
    try:
        report = doctor(("off.probe",), context=DoctorContext(ports=()), off=True)
        assert _status(report, "off.probe") == "ok", report

        actual = [
            ProcessEntry(
                pid=4244, uid=os.getuid(), argv=("/usr/local/bin/telegram-mcpd", "--serve")
            )
        ]
        doctor_module._list_processes_ps = lambda: actual
        loud = doctor(("off.probe",), context=DoctorContext(ports=()), off=True)
        assert _status(loud, "off.probe") == "fail", loud
    finally:
        doctor_module._list_processes_ps = original
