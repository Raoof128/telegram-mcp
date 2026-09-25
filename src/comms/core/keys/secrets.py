"""The secret store: provider credentials in daemon-owned 0600 versioned files (A13 rev 2, N7).

``<runtime-dir>/secrets/<item>/<version>``. Items are exactly the opaque and raw purposes of
the key inventory (provider credentials, TLS keys, the comms.db key); signing and MAC keys
live in key slots.
There is no Keychain backend in v0.3: the ``security`` CLI cannot take a secret on stdin
(``-w -`` stores a literal ``-``; measured, N7, R-B11) and the System Keychain needs root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from comms.core.keys.files import VersionedFiles
from comms.core.keys.purposes import PURPOSES

__all__ = ["SECRET_ITEMS", "FileSecretStore", "SecretStore", "SecretStoreError"]

# Provider credentials and TLS keys (opaque), and the comms.db key (raw 256-bit, A14).
SECRET_ITEMS = frozenset(name for name, p in PURPOSES.items() if p.kind in {"opaque", "raw256"})


class SecretStoreError(Exception):
    """A secret-store operation was refused. Fixed messages, never material."""


class SecretStore(Protocol):
    def put(self, item: str, version: int, value: bytes) -> None: ...
    def get(self, item: str, version: int) -> bytes: ...
    def delete(self, item: str, version: int) -> None: ...
    def versions(self, item: str) -> list[int]: ...


class FileSecretStore:
    def __init__(self, directory: Path) -> None:
        self._files = VersionedFiles(Path(directory), error=SecretStoreError, noun="secret")

    def __repr__(self) -> str:
        return "FileSecretStore(<redacted>)"

    @staticmethod
    def _check(item: str) -> str:
        if not isinstance(item, str) or item not in SECRET_ITEMS:
            raise SecretStoreError("unknown secret item")
        return item

    def put(self, item: str, version: int, value: bytes) -> None:
        if not isinstance(version, int) or version < 1:
            raise SecretStoreError("secret version must be a positive integer")
        self._files.write(self._check(item), version, value)

    def get(self, item: str, version: int) -> bytes:
        return self._files.read(self._check(item), version)

    def delete(self, item: str, version: int) -> None:
        self._files.delete(self._check(item), version)

    def versions(self, item: str) -> list[int]:
        return self._files.versions(self._check(item))
