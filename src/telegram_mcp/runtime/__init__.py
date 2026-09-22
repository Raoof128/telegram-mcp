"""On-demand runtime: kernel lock, ordered lifecycle, three-job launcher."""

from telegram_mcp.runtime.bootstrap import (
    FakeJobControl,
    JobControl,
    LaunchctlJobControl,
    ProcessEntry,
    bootstrap_status,
    ports_for_mode,
    request_stop,
    start_all,
    status,
    stop_all,
    sweep_strays,
)
from telegram_mcp.runtime.lifecycle import (
    RuntimeContext,
    StartupFailed,
    drain,
    run_lifecycle,
    shutdown,
    startup,
)
from telegram_mcp.runtime.lock import LockHandle, RuntimeActive, acquire_lock

__all__ = [
    "FakeJobControl",
    "JobControl",
    "LaunchctlJobControl",
    "LockHandle",
    "ProcessEntry",
    "RuntimeActive",
    "RuntimeContext",
    "StartupFailed",
    "acquire_lock",
    "bootstrap_status",
    "drain",
    "ports_for_mode",
    "request_stop",
    "run_lifecycle",
    "shutdown",
    "start_all",
    "startup",
    "status",
    "stop_all",
    "sweep_strays",
]
