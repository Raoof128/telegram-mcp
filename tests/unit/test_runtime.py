"""Task 1: runtime lock, lifecycle drain/startup/shutdown, bootstrap launcher tests."""

import socket as stdlib_socket

import pytest

from comms.transports.telegram.runtime.lock import RuntimeActive, acquire_lock


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
    from comms.transports.telegram.runtime.lifecycle import RuntimeContext, drain

    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=0.0)
    result = await drain(ctx, new_call=lambda: "INTERNAL_ERROR")
    assert result == "INTERNAL_ERROR"


async def test_drain_cancels_inflight_after_grace():
    import asyncio as _asyncio

    from comms.transports.telegram.runtime.lifecycle import RuntimeContext, drain

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


def test_startup_runs_fifteen_steps_in_order_with_seams():
    from comms.transports.telegram.runtime.lifecycle import STARTUP_STEPS, startup

    assert len(STARTUP_STEPS) == 15
    assert not [step for step in STARTUP_STEPS if "consent" in step]
    # The design's two load-bearing orderings (§1): the single-runtime lock
    # is taken before any secret is read or the database is opened, and the
    # tunnel client starts only after READY is advertised.
    assert STARTUP_STEPS.index("mint_runtime_id") == 0
    assert STARTUP_STEPS.index("acquire_lock") == 1
    for later in ("load_secrets", "open_db", "run_migrations", "gc_cursors"):
        assert STARTUP_STEPS.index(later) > STARTUP_STEPS.index("acquire_lock"), later
    assert STARTUP_STEPS.index("start_tunnel") == 14  # step 15, 1-based
    assert STARTUP_STEPS.index("mark_ready") < STARTUP_STEPS.index("start_tunnel")
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
        open_listeners=recorder("open_listeners"),
        start_tunnel=recorder("start_tunnel"),
    )
    assert ctx.state == "READY"
    assert len(ctx.runtime_id) == 16
    assert executed == [
        "load_secrets",
        "open_db",
        "open_listeners",
        "start_tunnel",
    ]


def test_startup_failure_aborts_before_ready_with_fixed_error():
    from comms.transports.telegram.runtime.lifecycle import StartupFailed, startup

    def boom(ctx):
        raise OSError("disk gone")

    with pytest.raises(StartupFailed) as excinfo:
        startup(None, mode="local", open_db=boom)
    assert "open_db" in str(excinfo.value)
    assert "disk gone" not in str(excinfo.value)


async def test_shutdown_disconnects_without_logout_and_releases_lock(tmp_path):
    from comms.transports.telegram.runtime.lifecycle import RuntimeContext, shutdown
    from comms.transports.telegram.runtime.lock import acquire_lock

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
    await shutdown(ctx, telegram=adapter, db=None, sockets=[sock_path])
    assert adapter.disconnected is True
    assert not sock_path.exists()
    # Lock released: a fresh acquire must succeed.
    reacquired = acquire_lock(tmp_path / "runtime.lock")
    reacquired.release()


def test_ports_for_mode_mapping_is_frozen():
    from comms.transports.telegram.runtime.bootstrap import ports_for_mode

    assert ports_for_mode("local") == (8766,)
    assert ports_for_mode("chatgpt") == (8767,)
    assert ports_for_mode("all") == (8766, 8767)
    with pytest.raises(ValueError):
        ports_for_mode("wide-open")


def test_launcher_starts_tunnel_only_after_ready_and_stops_in_order(tmp_path, monkeypatch):
    from comms.transports.telegram.runtime import bootstrap as bootstrap_mod
    from comms.transports.telegram.runtime.bootstrap import (
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
    result = start_all(mode="all", job_control=jobs, wait_ready=wait_ready, list_processes=list)
    assert result["ports"] == (8766, 8767)
    assert order.index("ready-observed") < order.index(f"start:{TUNNEL_LABEL}")
    assert jobs.job_state(RUNTIME_LABEL) == "running"

    reported = status(job_control=jobs)
    assert reported["jobs"][RUNTIME_LABEL] == "running"
    assert reported["jobs"][TUNNEL_LABEL] == "running"
    assert "challenge" not in str(reported).lower()
    assert "seed" not in str(reported).lower()

    # stop_all is gated on lock authority: hold the kernel lock so the
    # runtime counts as live (STALE: lock held, no admin socket).
    handle = acquire_lock(tmp_path / "runtime.lock")
    monkeypatch.setattr(
        bootstrap_mod, "request_stop", lambda timeout=5.0, *, runtime_dir=None: None
    )
    try:
        stop_all(job_control=jobs, runtime_dir=tmp_path)
    finally:
        handle.release()
    kinds = [c[0] for c in jobs.calls if c[0] == "stop"]
    stopped_labels = [c[1] for c in jobs.calls if c[0] == "stop"]
    assert kinds, "expected stop calls"
    # Stop order: tunnel intake off first, runtime drain second.
    assert stopped_labels == [TUNNEL_LABEL, RUNTIME_LABEL]


def test_stop_while_off_is_noop_success(tmp_path, monkeypatch):
    from comms.transports.telegram.runtime import bootstrap as bootstrap_mod

    monkeypatch.setenv("TELEGRAM_MCP_RUNTIME_DIR", str(tmp_path))
    assert bootstrap_mod.bootstrap_status()["state"] == "OFF"
    assert bootstrap_mod.request_stop(1.0) is None


def test_stale_lock_reports_off_and_live_lock_without_socket_reports_stale(tmp_path, monkeypatch):
    from comms.transports.telegram.runtime import bootstrap as bootstrap_mod

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

    from comms.transports.telegram.runtime.lifecycle import run_lifecycle

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

    from comms.transports.telegram.runtime.lifecycle import RuntimeContext, drain

    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=0.0)
    assert _asyncio.run(drain(ctx, grace=0.01)) == "INTERNAL_ERROR"


def test_sweep_strays_kills_only_exact_argv_uid_matches():
    import os as _os

    from comms.transports.telegram.runtime.bootstrap import (
        RUNTIME_LABEL,
        FakeJobControl,
        ProcessEntry,
        sweep_strays,
    )

    table = [
        ProcessEntry(pid=101, uid=501, argv=("telegram-mcpd", "--serve")),
        ProcessEntry(pid=102, uid=501, argv=("my-telegram-mcpd-wrapper",)),
        ProcessEntry(pid=103, uid=502, argv=("telegram-mcpd", "--serve")),
        ProcessEntry(pid=_os.getpid(), uid=501, argv=("telegram-mcpd",)),
        # The retired consent agent (comms spec v0.2) is no longer a job here.
        ProcessEntry(pid=104, uid=501, argv=("telegram-mcp-agent",)),
    ]
    jobs = FakeJobControl()
    killed = sweep_strays(
        "local",
        job_control=jobs,
        list_processes=lambda: table,
        uid_of=lambda label: 501,
    )
    assert killed == [101]
    assert ("terminate", 101, RUNTIME_LABEL) in jobs.calls
    assert all(call[1] not in (102, 103, 104, _os.getpid()) for call in jobs.calls)


def test_sweep_strays_skips_tunnel_in_local_mode():
    from comms.transports.telegram.runtime.bootstrap import (
        TUNNEL_LABEL,
        FakeJobControl,
        ProcessEntry,
        sweep_strays,
    )

    table = [ProcessEntry(pid=201, uid=501, argv=("telegram-mcp-tunnel",))]
    jobs = FakeJobControl()
    assert (
        sweep_strays(
            "local",
            job_control=jobs,
            list_processes=lambda: table,
            uid_of=lambda label: 501,
        )
        == []
    )
    assert jobs.calls == []
    killed = sweep_strays(
        "all",
        job_control=jobs,
        list_processes=lambda: table,
        uid_of=lambda label: 501,
    )
    assert killed == [201]
    assert ("terminate", 201, TUNNEL_LABEL) in jobs.calls


def test_start_all_sweeps_strays_before_kickstart(tmp_path):
    from comms.transports.telegram.runtime.bootstrap import (
        RUNTIME_LABEL,
        FakeJobControl,
        ProcessEntry,
        start_all,
    )

    jobs = FakeJobControl()
    table = [ProcessEntry(pid=301, uid=501, argv=("telegram-mcpd", "--serve"))]
    start_all(
        mode="local",
        job_control=jobs,
        wait_ready=lambda: True,
        list_processes=lambda: table,
        uid_of=lambda label: 501,
    )
    kinds = [(c[0], c[2] if len(c) > 2 else c[1]) for c in jobs.calls]
    assert kinds[0] == ("terminate", RUNTIME_LABEL)
    assert ("start", RUNTIME_LABEL) in kinds


def test_stop_all_drains_runtime_before_stop(tmp_path, monkeypatch):
    from comms.transports.telegram.runtime import bootstrap as bootstrap_mod

    handle = acquire_lock(tmp_path / "runtime.lock")
    try:
        jobs = bootstrap_mod.FakeJobControl()
        for label in (
            bootstrap_mod.RUNTIME_LABEL,
            bootstrap_mod.TUNNEL_LABEL,
        ):
            jobs.states[label] = "running"
        events = []
        orig_stop = jobs.stop_job

        def traced_stop(label):
            events.append(("stop", label))
            orig_stop(label)

        jobs.stop_job = traced_stop

        def fake_request_stop(timeout=5.0, *, runtime_dir=None):
            events.append(("drain",))

        monkeypatch.setattr(bootstrap_mod, "request_stop", fake_request_stop)
        result = bootstrap_mod.stop_all(job_control=jobs, runtime_dir=tmp_path)
        assert events == [
            ("stop", bootstrap_mod.TUNNEL_LABEL),
            ("drain",),
            ("stop", bootstrap_mod.RUNTIME_LABEL),
        ]
        assert result["stopped"] == [
            bootstrap_mod.TUNNEL_LABEL,
            bootstrap_mod.RUNTIME_LABEL,
        ]
    finally:
        handle.release()


def test_stop_all_ignores_drain_errors(tmp_path, monkeypatch):
    from comms.transports.telegram.runtime import bootstrap as bootstrap_mod

    handle = acquire_lock(tmp_path / "runtime.lock")
    try:
        jobs = bootstrap_mod.FakeJobControl()
        for label in (
            bootstrap_mod.RUNTIME_LABEL,
            bootstrap_mod.TUNNEL_LABEL,
        ):
            jobs.states[label] = "running"

        def boom(timeout=5.0, *, runtime_dir=None):
            raise OSError("socket gone")

        monkeypatch.setattr(bootstrap_mod, "request_stop", boom)
        result = bootstrap_mod.stop_all(job_control=jobs, runtime_dir=tmp_path)
        assert result["state"] == "OFF"
        assert result["stopped"] == [
            bootstrap_mod.TUNNEL_LABEL,
            bootstrap_mod.RUNTIME_LABEL,
        ]
    finally:
        handle.release()


def test_stop_all_noop_gated_on_lock_authority_not_job_states(tmp_path):
    from comms.transports.telegram.runtime import bootstrap as bootstrap_mod

    # Jobs claim running, but no live kernel lock holds the runtime dir:
    # lock-authority says OFF, so stop is a no-op with zero stop calls.
    jobs = bootstrap_mod.FakeJobControl()
    for label in (
        bootstrap_mod.RUNTIME_LABEL,
        bootstrap_mod.TUNNEL_LABEL,
    ):
        jobs.states[label] = "running"
    result = bootstrap_mod.stop_all(job_control=jobs, runtime_dir=tmp_path)
    assert result == {"state": "OFF", "stopped": []}
    assert jobs.calls == []
