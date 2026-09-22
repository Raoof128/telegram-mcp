"""Task 1: runtime lock, lifecycle drain/startup/shutdown, bootstrap launcher tests."""

import socket as stdlib_socket

import pytest

from telegram_mcp.runtime.lock import RuntimeActive, acquire_lock


def test_double_start_fails_closed(tmp_path):
    first = acquire_lock(tmp_path / "runtime.lock")
    try:
        with pytest.raises(RuntimeActive):
            acquire_lock(tmp_path / "runtime.lock")
    finally:
        first.release()


def test_released_lock_is_reacquirable(tmp_path):
    acquire_lock(tmp_path / "runtime.lock").release()
    handle = acquire_lock(tmp_path / "runtime.lock")
    handle.release()


def test_stale_socket_unlinked_only_after_lock_held(tmp_path, monkeypatch):
    """A dead process's socket file must not block a new owner.

    Pre-create a stale socket-path file with no live owner, acquire the
    kernel lock, then prove a fresh AF_UNIX bind succeeds (the stale file
    was unlinked only after the lock was held — never probed before).
    Uses a relative socket name: macOS AF_UNIX paths are capped at 104
    bytes and pytest tmp dirs exceed that.
    """
    monkeypatch.chdir(tmp_path)
    sock_path = tmp_path / "admin.sock"
    sock_path.write_bytes(b"stale-bytes-from-dead-process")
    handle = acquire_lock(tmp_path / "runtime.lock", socket_paths=[sock_path])
    try:
        server = stdlib_socket.socket(stdlib_socket.AF_UNIX, stdlib_socket.SOCK_STREAM)
        try:
            server.bind("admin.sock")
        finally:
            server.close()
    finally:
        handle.release()


def test_lock_diagnostics_never_confer_ownership(tmp_path):
    """Tampering with lockfile PID bytes must not unlock a live owner."""
    first = acquire_lock(tmp_path / "runtime.lock")
    try:
        (tmp_path / "runtime.lock").write_text(
            '{"pid": 1, "runtime_id": "00", "started_at": 0.0, "mode": "local"}'
        )
        with pytest.raises(RuntimeActive):
            acquire_lock(tmp_path / "runtime.lock")
    finally:
        first.release()


async def test_draining_rejects_new_calls():
    from telegram_mcp.runtime.lifecycle import RuntimeContext, drain

    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=0.0)
    result = await drain(ctx, new_call=lambda: "INTERNAL_ERROR")
    assert result == "INTERNAL_ERROR"


async def test_drain_cancels_inflight_after_grace():
    import asyncio as _asyncio

    from telegram_mcp.runtime.lifecycle import RuntimeContext, drain

    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=0.0)
    started = _asyncio.Event()
    cancelled = _asyncio.Event()

    async def inflight():
        started.set()
        try:
            await _asyncio.sleep(60)
        except _asyncio.CancelledError:
            cancelled.set()
            raise

    task = _asyncio.create_task(inflight())
    await started.wait()
    result = await drain(ctx, grace=0.01, new_call=lambda: "INTERNAL_ERROR", inflight=[task])
    assert result == "INTERNAL_ERROR"
    assert cancelled.is_set()
    assert ctx.state == "DRAINING"


def test_startup_runs_seventeen_steps_in_order_with_seams():
    from telegram_mcp.runtime.lifecycle import STARTUP_STEPS, startup

    assert len(STARTUP_STEPS) == 17
    assert STARTUP_STEPS.index("start_tunnel") == 15  # step 16, 1-based
    executed = []

    def recorder(name):
        def _step(ctx):
            executed.append(name)

        return _step

    ctx = startup(
        None,
        mode="local",
        load_secrets=recorder("load_secrets"),
        open_db=recorder("open_db"),
        handshake_consent=recorder("handshake_consent"),
        open_listeners=recorder("open_listeners"),
        start_tunnel=recorder("start_tunnel"),
    )
    assert ctx.state == "READY"
    assert len(ctx.runtime_id) == 16
    assert executed == [
        "load_secrets",
        "open_db",
        "handshake_consent",
        "open_listeners",
        "start_tunnel",
    ]


def test_startup_failure_aborts_before_ready_with_fixed_error():
    from telegram_mcp.runtime.lifecycle import StartupFailed, startup

    def boom(ctx):
        raise OSError("disk gone")

    with pytest.raises(StartupFailed) as excinfo:
        startup(None, mode="local", open_db=boom)
    assert "open_db" in str(excinfo.value)
    assert "disk gone" not in str(excinfo.value)


async def test_shutdown_disconnects_without_logout_and_releases_lock(tmp_path):
    from telegram_mcp.runtime.lifecycle import RuntimeContext, shutdown
    from telegram_mcp.runtime.lock import acquire_lock

    class FakeTelegramAdapter:
        def __init__(self):
            self.disconnected = False

        def disconnect(self):
            self.disconnected = True

    adapter = FakeTelegramAdapter()
    assert not hasattr(adapter, "log_out")

    handle = acquire_lock(tmp_path / "runtime.lock")
    sock_path = tmp_path / "admin.sock"
    sock_path.write_bytes(b"x")
    ctx = RuntimeContext(runtime_id=b"\x01" * 16, started_at=1.0)
    ctx.lock = handle
    await shutdown(ctx, telegram=adapter, db=None, sockets=[sock_path], consent_ui=None)
    assert adapter.disconnected is True
    assert not sock_path.exists()
    # Lock released: a fresh acquire must succeed.
    reacquired = acquire_lock(tmp_path / "runtime.lock")
    reacquired.release()


def test_ports_for_mode_mapping_is_frozen():
    from telegram_mcp.runtime.bootstrap import ports_for_mode

    assert ports_for_mode("local") == (8766,)
    assert ports_for_mode("chatgpt") == (8767,)
    assert ports_for_mode("all") == (8766, 8767)
    with pytest.raises(ValueError):
        ports_for_mode("wide-open")


def test_launcher_starts_tunnel_only_after_ready_and_stops_in_order():
    from telegram_mcp.runtime.bootstrap import (
        AGENT_LABEL,
        RUNTIME_LABEL,
        TUNNEL_LABEL,
        FakeJobControl,
        start_all,
        status,
        stop_all,
    )

    jobs = FakeJobControl()
    order = []

    def wait_ready():
        order.append("ready-observed")
        return True

    orig_start = jobs.start_job

    def traced_start(label):
        order.append(f"start:{label}")
        orig_start(label)

    jobs.start_job = traced_start
    result = start_all(mode="all", job_control=jobs, wait_ready=wait_ready)
    assert result["ports"] == (8766, 8767)
    assert order.index("ready-observed") < order.index(f"start:{TUNNEL_LABEL}")
    assert jobs.job_state(RUNTIME_LABEL) == "running"

    reported = status(job_control=jobs)
    assert reported["jobs"][RUNTIME_LABEL] == "running"
    assert reported["jobs"][TUNNEL_LABEL] == "running"
    assert "challenge" not in str(reported).lower()
    assert "seed" not in str(reported).lower()

    stop_all(job_control=jobs)
    kinds = [c[0] for c in jobs.calls if c[0] == "stop"]
    stopped_labels = [c[1] for c in jobs.calls if c[0] == "stop"]
    assert kinds, "expected stop calls"
    # Stop order: tunnel intake off first, runtime drain second, agent last.
    assert stopped_labels == [TUNNEL_LABEL, RUNTIME_LABEL, AGENT_LABEL]


def test_stop_while_off_is_noop_success(tmp_path, monkeypatch):
    from telegram_mcp.runtime import bootstrap as bootstrap_mod

    monkeypatch.setenv("TELEGRAM_MCP_RUNTIME_DIR", str(tmp_path))
    assert bootstrap_mod.bootstrap_status()["state"] == "OFF"
    assert bootstrap_mod.request_stop(1.0) is None


def test_stale_lock_reports_off_and_live_lock_without_socket_reports_stale(tmp_path, monkeypatch):
    from telegram_mcp.runtime import bootstrap as bootstrap_mod

    monkeypatch.setenv("TELEGRAM_MCP_RUNTIME_DIR", str(tmp_path))
    assert bootstrap_mod.bootstrap_status()["state"] == "OFF"
    handle = acquire_lock(tmp_path / "runtime.lock")
    try:
        assert bootstrap_mod.bootstrap_status()["state"] == "STALE"
    finally:
        handle.release()
    assert bootstrap_mod.bootstrap_status()["state"] == "OFF"


def test_run_lifecycle_starts_up_and_drains_on_stop(tmp_path):
    import threading

    from telegram_mcp.runtime.lifecycle import run_lifecycle

    stopped = threading.Event()
    stopped.set()
    seen = []
    run_lifecycle(
        None,
        mode="local",
        stopped=stopped,
        lock_path=tmp_path / "runtime.lock",
        open_db=lambda ctx: seen.append("open_db"),
    )
    assert seen == ["open_db"]
    assert (tmp_path / "runtime.lock").exists()


def test_drain_without_explicit_new_call_returns_fixed_internal_error():
    import asyncio as _asyncio

    from telegram_mcp.runtime.lifecycle import RuntimeContext, drain

    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=0.0)
    assert _asyncio.run(drain(ctx, grace=0.01)) == "INTERNAL_ERROR"
