"""Interactive launcher: orchestrate three launchd jobs, never spawn siblings.

The operator-owned ``telegram-mcp`` process drives three independent
launchd jobs via a :class:`JobControl` seam:

- runtime as ``telegram-mcpd`` via ``system/<label>``,
- consent agent in the GUI session via ``gui/$UID/<label>``,
- tunnel as ``telegram-mcp-tunnel`` via ``system/<label>`` (ChatGPT modes only).

Escalation is interactive sudo to ``launchctl kickstart/stop`` per job
(admin password or Touch ID; never passwordless). Production backend is
:class:`LaunchctlJobControl`; headless tests use :class:`FakeJobControl`.

Port mapping is frozen: ``local`` opens 8766 only, ``chatgpt`` opens 8767
only, ``all`` opens both. Tunnel start happens only after READY.

Host-mutating actions (real kickstart/stop) are OPERATOR-PENDING: this
module implements them but they are never exercised here.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from comms.transports.telegram import __version__

__all__ = [
    "ADMIN_SOCK_NAME",
    "AGENT_LABEL",
    "CONSENT_SOCK_NAME",
    "LOCK_FILENAME",
    "RUNTIME_LABEL",
    "TUNNEL_LABEL",
    "FakeJobControl",
    "JobControl",
    "LaunchctlJobControl",
    "ProcessEntry",
    "bootstrap_status",
    "ports_for_mode",
    "request_stop",
    "start_all",
    "status",
    "stop_all",
    "sweep_strays",
]

LOCK_FILENAME = "runtime.lock"
ADMIN_SOCK_NAME = "admin.sock"
CONSENT_SOCK_NAME = "consent.sock"

RUNTIME_LABEL = "telegram-mcpd"
AGENT_LABEL = "telegram-mcp-agent"
TUNNEL_LABEL = "telegram-mcp-tunnel"

LOCAL_PORT = 8766
CHATGPT_PORT = 8767

MODE_PORTS: dict[str, tuple[int, ...]] = {
    "local": (LOCAL_PORT,),
    "chatgpt": (CHATGPT_PORT,),
    "all": (LOCAL_PORT, CHATGPT_PORT),
}

_DEFAULT_RUNTIME_DIR = Path("/private/var/run/telegram-mcp")
_RUNTIME_DIR_ENV = "TELEGRAM_MCP_RUNTIME_DIR"
_ADMIN_QUERY_TIMEOUT = 1.0


class JobControl(Protocol):
    """Seam for per-job launchd orchestration."""

    def start_job(self, label: str) -> None: ...
    def stop_job(self, label: str) -> None: ...
    def job_state(self, label: str) -> str: ...
    def terminate_stray(self, pid: int, label: str) -> None: ...


class LaunchctlJobControl:
    """Production backend: interactive sudo to launchctl per job.

    Never passwordless (no ``-n``): sudo prompts for the admin password
    or Touch ID. OPERATOR-PENDING: never run from automated tests.
    """

    def _domain(self, label: str) -> str:
        if label == AGENT_LABEL:
            return f"gui/{os.getuid()}/{label}"
        return f"system/{label}"

    def start_job(self, label: str) -> None:
        subprocess.run(
            ["sudo", "launchctl", "kickstart", self._domain(label)],
            check=True,
        )

    def stop_job(self, label: str) -> None:
        subprocess.run(
            ["sudo", "launchctl", "stop", self._domain(label)],
            check=True,
        )

    def job_state(self, label: str) -> str:
        try:
            out = subprocess.run(
                ["launchctl", "print", self._domain(label)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return "unknown"
        if out.returncode != 0:
            return "stopped"
        return "running" if "state = running" in out.stdout else "stopped"

    def terminate_stray(self, pid: int, label: str) -> None:
        """SIGTERM a stray with the job's own escalation (sudo for system jobs).

        OPERATOR-PENDING: never run from automated tests.
        """
        if label == AGENT_LABEL:
            subprocess.run(["kill", "-TERM", str(pid)], check=True)
        else:
            subprocess.run(["sudo", "kill", "-TERM", str(pid)], check=True)


class FakeJobControl:
    """Headless test backend: in-memory job states + call ledger."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}
        self.calls: list[tuple[Any, ...]] = []

    def start_job(self, label: str) -> None:
        self.calls.append(("start", label))
        self.states[label] = "running"

    def stop_job(self, label: str) -> None:
        self.calls.append(("stop", label))
        self.states[label] = "stopped"

    def job_state(self, label: str) -> str:
        return self.states.get(label, "stopped")

    def terminate_stray(self, pid: int, label: str) -> None:
        """Test seam: ledger the kill, never signal a real process."""
        self.calls.append(("terminate", pid, label))


def ports_for_mode(mode: str) -> tuple[int, ...]:
    """Frozen mode -> TCP ports mapping."""
    try:
        return MODE_PORTS[mode]
    except KeyError:
        raise ValueError(f"unknown mode {mode!r}") from None


def _runtime_dir(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(_RUNTIME_DIR_ENV)
    return Path(env) if env else _DEFAULT_RUNTIME_DIR


def _read_diagnostics(lock_path: Path) -> dict[str, Any]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def bootstrap_status(
    *,
    runtime_dir: str | Path | None = None,
    job_control: JobControl | None = None,
) -> dict[str, Any]:
    """Aggregate runtime status. Works while OFF.

    Liveness comes from the kernel-lock probe only (PID bytes are
    diagnostics): absent/stale lockfile -> OFF; live owner + reachable
    admin socket -> READY; live owner + unreachable -> STALE.
    """
    from comms.transports.telegram.runtime.lock import probe_live_owner

    directory = _runtime_dir(runtime_dir)
    lock_path = directory / LOCK_FILENAME
    admin_path = directory / ADMIN_SOCK_NAME
    report: dict[str, Any] = {
        "state": "OFF",
        "mode": None,
        "version": __version__,
        "pid": None,
        "ports": (),
        "jobs": {RUNTIME_LABEL: "unknown", AGENT_LABEL: "unknown", TUNNEL_LABEL: "unknown"},
    }
    if job_control is not None:
        report["jobs"] = {
            RUNTIME_LABEL: job_control.job_state(RUNTIME_LABEL),
            AGENT_LABEL: job_control.job_state(AGENT_LABEL),
            TUNNEL_LABEL: job_control.job_state(TUNNEL_LABEL),
        }
    if not lock_path.exists() or not probe_live_owner(lock_path):
        return report
    diagnostics = _read_diagnostics(lock_path)
    report["pid"] = diagnostics.get("pid")
    report["mode"] = diagnostics.get("mode")
    mode = diagnostics.get("mode")
    if mode in MODE_PORTS:
        report["ports"] = MODE_PORTS[mode]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_ADMIN_QUERY_TIMEOUT)
            sock.connect(str(admin_path))
    except OSError:
        report["state"] = "STALE"
    else:
        report["state"] = "READY"
    return report


def status(
    job_control: JobControl | None = None,
    *,
    runtime_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate the three job states + mode + pid. Never secrets."""
    report = bootstrap_status(runtime_dir=runtime_dir, job_control=job_control)
    text = json.dumps(report)
    for forbidden in ("seed", "challenge", "private", "secret", "token"):
        assert forbidden not in text.lower(), f"status leaked {forbidden}"
    return report


@dataclass(frozen=True)
class ProcessEntry:
    """One row of the process table: pid, owner UID, and argv vector."""

    pid: int
    uid: int
    argv: tuple[str, ...]


# Stray-process markers per job: a process is a stray of a job only when
# the marker is an *exact element* of its argv vector AND its UID matches
# the job's UID exactly. Substring binary names never match. The agent and
# tunnel exact argv vectors are pinned with the Task 8 / Plan 2b plists;
# until then the label-named binary is the marker (fail closed on doubt).
JOB_STRAY_MARKERS: dict[str, str] = {
    RUNTIME_LABEL: "telegram-mcpd",
    AGENT_LABEL: "telegram-mcp-agent",
    TUNNEL_LABEL: "telegram-mcp-tunnel",
}


def _default_uid_of(label: str) -> int | None:
    """Resolve the UID a job's strays must run as. None skips the sweep."""
    if label == AGENT_LABEL:
        return os.getuid()
    try:
        import pwd

        return pwd.getpwnam(label).pw_uid
    except KeyError:
        return None


def _list_processes_ps() -> list[ProcessEntry]:
    """Read-only `ps` snapshot. OPERATOR-PENDING for live verification."""
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "pid=,uid=,command="],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    entries: list[ProcessEntry] = []
    for line in out.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, uid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        try:
            argv = tuple(shlex.split(parts[2]))
        except ValueError:
            continue
        entries.append(ProcessEntry(pid=pid, uid=uid, argv=argv))
    return entries


def sweep_strays(
    mode: str,
    *,
    job_control: JobControl,
    list_processes: Callable[[], list[ProcessEntry]] | None = None,
    uid_of: Callable[[str], int | None] | None = None,
) -> list[int]:
    """SIGTERM stray processes holding ports/sockets outside launchd.

    Match rule is exact argv element + exact UID only; our own PID is never
    matched. Kills go through the job's own escalation (sudo for the system
    jobs, plain kill for the GUI agent). Runtime + agent always swept; the
    tunnel only in ChatGPT modes. Returns terminated PIDs.
    """
    ports_for_mode(mode)
    table_fn = list_processes or _list_processes_ps
    uid_fn = uid_of or _default_uid_of
    labels = [RUNTIME_LABEL, AGENT_LABEL]
    if mode in ("chatgpt", "all"):
        labels.append(TUNNEL_LABEL)
    try:
        table = table_fn()
    except OSError:
        return []
    self_pid = os.getpid()
    killed: list[int] = []
    for label in labels:
        uid = uid_fn(label)
        if uid is None:
            continue
        marker = JOB_STRAY_MARKERS[label]
        for entry in table:
            if entry.pid == self_pid or entry.uid != uid:
                continue
            if marker not in entry.argv:
                continue
            job_control.terminate_stray(entry.pid, label)
            killed.append(entry.pid)
    return killed


def _wait_until_ready(
    runtime_dir: str | Path | None,
    *,
    timeout: float = 30.0,
    poll: float = 0.2,
) -> bool:
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if bootstrap_status(runtime_dir=runtime_dir)["state"] == "READY":
            return True
        _time.sleep(poll)
    return False


def start_all(
    *,
    mode: str,
    job_control: JobControl,
    wait_ready: Any | None = None,
    runtime_dir: str | Path | None = None,
    list_processes: Callable[[], list[ProcessEntry]] | None = None,
    uid_of: Callable[[str], int | None] | None = None,
) -> dict[str, Any]:
    """Pre-start sweep, then kickstart runtime, agent, and tunnel (ChatGPT modes).

    Tunnel start happens strictly after READY is observed.
    """
    ports = ports_for_mode(mode)
    # Pre-start sweep: unload dead jobs (anything not already stopped),
    # then terminate stray processes holding ports/sockets outside launchd.
    for label in (RUNTIME_LABEL, AGENT_LABEL, TUNNEL_LABEL):
        if job_control.job_state(label) != "stopped":
            job_control.stop_job(label)
    sweep_strays(mode, job_control=job_control, list_processes=list_processes, uid_of=uid_of)
    job_control.start_job(RUNTIME_LABEL)
    ready = wait_ready() if wait_ready is not None else _wait_until_ready(runtime_dir)
    if not ready:
        raise RuntimeError("runtime did not reach READY")
    job_control.start_job(AGENT_LABEL)
    if mode in ("chatgpt", "all"):
        job_control.start_job(TUNNEL_LABEL)
    return {"state": "READY", "mode": mode, "ports": ports}


def stop_all(
    *,
    job_control: JobControl,
    runtime_dir: str | Path | None = None,
    stop_timeout: float = 5.0,
) -> dict[str, Any]:
    """Stop order: tunnel intake off -> runtime drain -> agent bootout.

    No-op success while OFF, where OFF is lock authority
    (``bootstrap_status``), never the job-state view.
    """
    if bootstrap_status(runtime_dir=runtime_dir)["state"] == "OFF":
        return {"state": "OFF", "stopped": []}
    states = {
        RUNTIME_LABEL: job_control.job_state(RUNTIME_LABEL),
        AGENT_LABEL: job_control.job_state(AGENT_LABEL),
        TUNNEL_LABEL: job_control.job_state(TUNNEL_LABEL),
    }
    stopped: list[str] = []
    for label in (TUNNEL_LABEL, RUNTIME_LABEL, AGENT_LABEL):
        if states[label] == "stopped":
            continue
        if label == RUNTIME_LABEL:
            # Graceful drain first; best-effort, socket errors ignored.
            try:
                request_stop(stop_timeout, runtime_dir=runtime_dir)
            except OSError:
                pass
        job_control.stop_job(label)
        stopped.append(label)
    directory = _runtime_dir(runtime_dir)
    for name in (ADMIN_SOCK_NAME, CONSENT_SOCK_NAME):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return {"state": "OFF", "stopped": stopped}


def _send_control_stop(admin_path: Path, timeout: float) -> None:
    """Framed ``{"control": "stop"}`` on the admin socket; ack read and dropped.

    The admin socket speaks the length-prefixed strict-JSON protocol in
    ``ipc.framing``; a bare sentinel would be read as a length header and
    refused, so the graceful stop path uses the real frame.
    """
    from comms.transports.telegram.ipc.framing import encode_json_frame

    payload = encode_json_frame({"control": "stop"})
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(admin_path))
        sock.sendall(len(payload).to_bytes(4, "big") + payload)
        try:
            sock.recv(4096)
        except OSError:
            pass  # a drained runtime may close before acking


def request_stop(
    timeout: float = 5.0,
    *,
    runtime_dir: str | Path | None = None,
) -> None:
    """Ask the runtime to drain via the admin socket (TERM fallback).

    No-op success while OFF.
    """
    import signal

    from comms.transports.telegram.runtime.lock import probe_live_owner

    directory = _runtime_dir(runtime_dir)
    if bootstrap_status(runtime_dir=directory)["state"] == "OFF":
        return
    admin_path = directory / ADMIN_SOCK_NAME
    try:
        _send_control_stop(admin_path, timeout)
    except OSError:
        # TERM fallback, guarded by a fresh kernel-lock liveness probe so
        # a recycled PID from diagnostics can never be signalled blindly.
        lock_path = directory / LOCK_FILENAME
        if not probe_live_owner(lock_path):
            return
        pid = _read_diagnostics(lock_path).get("pid")
        if isinstance(pid, int) and pid > 0 and probe_live_owner(lock_path):
            os.kill(pid, signal.SIGTERM)
