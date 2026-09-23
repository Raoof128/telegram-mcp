"""Task 9 Steps 1-2: CLI wiring, driven the way the plan requires.

The CLI has no test-only flags, so nothing here weakens a production path:
argument parsing is covered through ``--help``, the runtime verbs are
covered by driving ``bootstrap_status``/``serve_admin`` directly, and the
host-mutating verbs (``start``, ``stop``) are exercised only in their
argument handling.
"""

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from telegram_mcp.cli import main
from telegram_mcp.ipc.admin import AdminRouter, serve_admin

VERBS = ("demo", "start", "stop", "status", "doctor", "admin", "keys", "pair", "rotate")


def _run(argv, monkeypatch):
    monkeypatch.setattr("sys.argv", ["telegram-mcp", *argv])
    try:
        main()
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def test_status_shape_while_off(capsys):
    from telegram_mcp.runtime.bootstrap import bootstrap_status

    assert set(bootstrap_status()) >= {"state", "mode", "version"}


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_has_help(verb, monkeypatch, capsys):
    assert _run([verb, "--help"], monkeypatch) == 0
    assert verb in capsys.readouterr().out


def test_bare_invocation_is_a_usage_error(monkeypatch):
    assert _run([], monkeypatch) == 2


def test_status_verb_prints_the_off_report(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_RUNTIME_DIR", str(tmp_path))
    assert _run(["status"], monkeypatch) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "OFF"
    assert report["ports"] == []


def test_start_rejects_two_modes_at_once(monkeypatch):
    assert _run(["start", "--local", "--all"], monkeypatch) == 2


def test_doctor_verb_exits_nonzero_under_the_production_gate(monkeypatch, capsys):
    assert _run(["doctor", "--production"], monkeypatch) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["unmet_production_checks"]


def test_doctor_verb_passes_headless_without_the_gate(monkeypatch, capsys):
    assert _run(["doctor"], monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_keys_provision_then_list(monkeypatch, capsys, tmp_path):
    store = tmp_path / "keys"
    assert _run(["keys", "provision", "--store-dir", str(store)], monkeypatch) == 0
    provisioned = json.loads(capsys.readouterr().out)["provisioned"]
    assert set(provisioned) == {
        "principal-key",
        "cursor-key",
        "privacy-key",
        "challenge-key",
        "disclosure-key",
        "audit-checkpoint-key",
        "audit-chain-key",
    }
    assert _run(["keys", "list", "--store-dir", str(store)], monkeypatch) == 0
    listed = json.loads(capsys.readouterr().out)
    assert set(listed) == set(provisioned)
    assert all(":" in value for value in listed.values())
    # no private material reaches stdout
    for path in store.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_pair_export_import_and_verify_round_trip(monkeypatch, capsys, tmp_path):
    store = tmp_path / "keys"
    assert _run(["keys", "provision", "--store-dir", str(store)], monkeypatch) == 0
    capsys.readouterr()
    assert _run(["pair", "export", "challenge-key", "--store-dir", str(store)], monkeypatch) == 0
    exported = json.loads(capsys.readouterr().out)["public_b64url"]
    assert (
        _run(
            ["pair", "import", "agent-transport-key", exported, "--store-dir", str(store)],
            monkeypatch,
        )
        == 0
    )
    fingerprint = json.loads(capsys.readouterr().out)["fingerprint"]
    assert fingerprint.startswith("ed25519:")
    assert (
        _run(
            ["pair", "verify", "agent-transport-key", fingerprint, "--store-dir", str(store)],
            monkeypatch,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["match"] is True
    assert (
        _run(
            [
                "pair",
                "verify",
                "agent-transport-key",
                "ed25519:" + "0" * 64,
                "--store-dir",
                str(store),
            ],
            monkeypatch,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["match"] is False


def test_pair_import_without_a_value_is_a_usage_error(monkeypatch, tmp_path):
    store = tmp_path / "keys"
    assert _run(["keys", "provision", "--store-dir", str(store)], monkeypatch) == 0
    assert (
        _run(["pair", "import", "agent-transport-key", "--store-dir", str(store)], monkeypatch) == 2
    )


def test_rotate_pins_then_rotates_the_tunnel_binding(monkeypatch, capsys, tmp_path):
    store = tmp_path / "keys"
    store.mkdir(mode=0o700)
    first = "spki:sha256:" + "a" * 64
    second = "spki:sha256:" + "b" * 64
    assert _run(["rotate", "tunnel-binding", first, "--store-dir", str(store)], monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["spki"] == first
    assert _run(["rotate", "tunnel-binding", second, "--store-dir", str(store)], monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["spki"] == second
    assert (
        _run(["rotate", "tunnel-binding", "nonsense", "--store-dir", str(store)], monkeypatch) == 1
    )


def test_admin_verb_reports_a_stopped_runtime(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # short relative path: AF_UNIX caps near 104 bytes
    assert _run(["admin", "lock", "status", "--runtime-dir", "run"], monkeypatch) == 3
    assert "not running" in capsys.readouterr().err


def test_admin_verb_rejects_malformed_arguments(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert _run(["admin", "lock", "--arg", "novalue", "--runtime-dir", "run"], monkeypatch) == 2


async def test_admin_verb_proxies_to_a_live_socket(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    router = AdminRouter(
        {
            "lock status": lambda args: {"locked": False, "security_epoch": 1},
            "lock": lambda args: {"locked": True},
        },
        presence_verifier=lambda proof: proof == {"method": "stub"},
    )
    server = await serve_admin(Path("run") / "admin.sock", router)
    try:
        code = await asyncio.to_thread(
            _run, ["admin", "lock", "status", "--runtime-dir", "run"], monkeypatch
        )
        assert code == 0
        assert json.loads(capsys.readouterr().out) == {"locked": False, "security_epoch": 1}

        # a routed but unimplemented command is refused, not faked
        code = await asyncio.to_thread(
            _run, ["admin", "project", "list", "--runtime-dir", "run"], monkeypatch
        )
        assert code == 5
        assert "NOT_AVAILABLE_IN_PHASE" in capsys.readouterr().err

        # presence-gated without a proof
        code = await asyncio.to_thread(_run, ["admin", "lock", "--runtime-dir", "run"], monkeypatch)
        assert code == 6
        assert "PRESENCE_REQUIRED" in capsys.readouterr().err

        # off the §33 surface entirely
        code = await asyncio.to_thread(
            _run, ["admin", "drop", "everything", "--runtime-dir", "run"], monkeypatch
        )
        assert code == 2
        assert "UNKNOWN_COMMAND" in capsys.readouterr().err
    finally:
        server.close()
        await server.wait_closed()


async def test_stop_reaches_the_framed_control_channel(monkeypatch, tmp_path):
    """request_stop must speak frames: the socket has no raw sentinel."""
    from telegram_mcp.runtime.bootstrap import request_stop
    from telegram_mcp.runtime.lock import acquire_lock

    monkeypatch.chdir(tmp_path)
    stops: list[dict] = []
    router = AdminRouter(control_handlers={"stop": lambda args: stops.append(args) or {"ok": 1}})
    Path("run").mkdir(mode=0o770, exist_ok=True)
    server = await serve_admin(Path("run") / "admin.sock", router)
    handle = acquire_lock(Path("run") / "runtime.lock")
    try:
        await asyncio.to_thread(request_stop, 2.0, runtime_dir="run")
        for _ in range(50):
            if stops:
                break
            await asyncio.sleep(0.02)
        assert stops == [{}]
    finally:
        handle.release()
        server.close()
        await server.wait_closed()


def test_demo_config_failure_prints_one_fixed_line(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_API_HASH", "not-allowed-in-safe-demo")
    code = _run(["demo", "--host", "0.0.0.0", "--port", "8766"], monkeypatch)
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.strip() == "telegram-mcp: invalid demo configuration."
    assert "ValidationError" not in captured.err
    assert "0.0.0.0" not in captured.err
    assert os.environ["TELEGRAM_API_HASH"] == "not-allowed-in-safe-demo"
