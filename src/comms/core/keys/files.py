"""Versioned, write-once 0600 files in 0700 directories owned by this process: one copy.

Key slots and the secret store both keep secrets as ``<root>/<name>/<version>``. A file is
created with ``O_EXCL`` (a version is never overwritten), fsynced, and its directory
fsynced. A root or item directory with any group/other bits, or owned by another uid, is
refused; so is a file that is not exactly 0600 and ours. Errors are fixed strings that
never contain material.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

__all__ = ["VersionedFiles"]


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private(st: os.stat_result, mode: int) -> bool:
    return stat.S_IMODE(st.st_mode) == mode and st.st_uid == os.getuid()


class VersionedFiles:
    def __init__(self, root: Path, *, error: type[Exception], noun: str) -> None:
        self._error, self._noun = error, noun
        root = Path(root)
        if root.exists():
            st = root.stat()
            if stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid():
                raise error(f"{noun} directory permissions refused")
        else:
            root.mkdir(mode=0o700, parents=True)
            os.chmod(root, 0o700)
        self._root = root

    def __repr__(self) -> str:
        return f"VersionedFiles({self._noun}, <redacted>)"

    def _item(self, name: str, *, create: bool = False) -> Path:
        path = self._root / name
        if path.exists():
            st = path.stat()
            if stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid():
                raise self._error(f"{self._noun} directory permissions refused")
        elif create:
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
            _fsync_dir(self._root)
        return path

    def versions(self, name: str) -> list[int]:
        path = self._item(name)
        if not path.exists():
            return []
        return sorted(int(p.name) for p in path.iterdir() if p.name.isdigit())

    def write(self, name: str, version: int, value: bytes) -> None:
        directory = self._item(name, create=True)
        path = directory / str(int(version))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise self._error(f"{self._noun} version exists") from None
        try:
            os.write(fd, value)
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(directory)

    def read(self, name: str, version: int) -> bytes:
        path = self._item(name) / str(int(version))
        try:
            st = path.stat()
        except FileNotFoundError:
            raise self._error(f"{self._noun} missing") from None
        if not _private(st, 0o600):
            raise self._error(f"{self._noun} permissions refused")
        return path.read_bytes()

    def delete(self, name: str, version: int) -> None:
        path = self._item(name) / str(int(version))
        try:
            path.unlink()
        except FileNotFoundError:
            return
        _fsync_dir(path.parent)
