"""Kernel-flock ownership for the on-demand runtime.

Ownership authority is the kernel-held advisory exclusive lock
(``fcntl.flock(LOCK_EX | LOCK_NB)``) on the runtime lockfile, held for the
process lifetime. A crash releases it automatically, so PID reuse can never
cause ambiguity. The lockfile *content* (pid, runtime_id, started_at, mode)
is diagnostics only, never authority.

Flow: open -> non-blocking exclusive lock (failure -> ``RuntimeActive``,
fail closed) -> only then unlink stale socket files. Sockets are never
probed before the lock is held.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Self

__all__ = ["LockHandle", "RuntimeActive", "acquire_lock", "probe_live_owner"]


class RuntimeActive(Exception):
    """A live runtime already holds the kernel lock. Fail closed."""


class LockHandle:
    """Holds the kernel exclusive lock until :meth:`release`."""

    def __init__(self, path: Path, fd: int) -> None:
        self._path = Path(path)
        self._fd = fd
        self._released = False

    @property
    def path(self) -> Path:
        return self._path

    def update_diagnostics(
        self,
        *,
        runtime_id: bytes | None = None,
        mode: str | None = None,
    ) -> None:
        """Rewrite diagnostics bytes. Never confers or extends ownership."""
        payload = {
            "pid": os.getpid(),
            "runtime_id": runtime_id.hex() if runtime_id is not None else None,
            "started_at": time.time(),
            "mode": mode,
        }
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, json.dumps(payload).encode("utf-8"))

    def release(self) -> None:
        """Release the kernel lock and close the descriptor."""
        if self._released:
            return
        self._released = True
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_lock(
    path: str | os.PathLike[str],
    *,
    socket_paths: tuple[str | os.PathLike[str], ...] | list[str | os.PathLike[str]] = (),
    runtime_id: bytes | None = None,
    mode: str | None = None,
) -> LockHandle:
    """Acquire the kernel exclusive lock on ``path``.

    Raises :class:`RuntimeActive` (fail closed) when another process holds
    the lock. Only after the lock is held are stale socket files unlinked,
    so a live owner's sockets can never be deleted.
    """
    lock_path = Path(path)
    if lock_path.parent and str(lock_path.parent):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise RuntimeActive("runtime lock is held by a live owner") from None
        raise
    handle = LockHandle(lock_path, fd)
    handle.update_diagnostics(runtime_id=runtime_id, mode=mode)
    # Stale-socket reclaim happens strictly after the lock is held.
    for sock in socket_paths:
        try:
            Path(sock).unlink()
        except FileNotFoundError:
            pass
    return handle


def probe_live_owner(path: str | os.PathLike[str]) -> bool:
    """Return True iff a live process currently holds the kernel lock.

    Opens a *separate* descriptor and attempts a non-blocking exclusive
    lock: success means no live owner (the probe lock is released
    immediately); EACCES/EAGAIN means a live owner holds it. Lockfile
    content is never consulted.
    """
    lock_path = Path(path)
    if not lock_path.exists():
        return False
    fd = os.open(lock_path, os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return True
            raise
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
    finally:
        os.close(fd)
