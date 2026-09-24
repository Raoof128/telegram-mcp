"""Key storage abstraction with PROVISION/REQUIRE separation. Keys are created
once (provision) and thereafter only loaded (require); require never
regenerates a missing key, because that would silently strand an existing
encrypted database under a new, wrong key (INV-ATREST)."""

import os
from typing import Protocol


class KeyStoreError(RuntimeError):
    pass


class KeyExists(KeyStoreError):
    pass


class KeyMissing(KeyStoreError):
    pass


class KeyStore(Protocol):
    def provision(self, name: str, nbytes: int) -> bytes: ...
    def require(self, name: str, nbytes: int) -> bytes: ...


class MemoryKeyStore:
    def __init__(self) -> None:
        self._d: dict[str, bytes] = {}

    def provision(self, name: str, nbytes: int) -> bytes:
        if name in self._d:
            raise KeyExists(name)
        self._d[name] = os.urandom(nbytes)
        return self._d[name]

    def require(self, name: str, nbytes: int) -> bytes:
        if name not in self._d:
            raise KeyMissing(name)
        key = self._d[name]
        if len(key) != nbytes:
            raise ValueError(f"key {name!r} has length {len(key)}, expected {nbytes}")
        return key


class KeyringKeyStore:
    """macOS Keychain via `keyring`. Refuses to run on a non-macOS backend so a
    fallback store (e.g. plaintext file) can never silently hold vault keys."""

    SERVICE = "whatsvault"

    def __init__(self) -> None:
        import keyring

        backend = type(keyring.get_keyring()).__module__
        if "macOS" not in backend and "keychain" not in backend.lower():
            raise KeyStoreError(f"expected macOS Keychain backend, got {backend!r}")

    def provision(self, name: str, nbytes: int) -> bytes:
        import keyring

        if keyring.get_password(self.SERVICE, name) is not None:
            raise KeyExists(name)
        key = os.urandom(nbytes)
        keyring.set_password(self.SERVICE, name, key.hex())
        return key

    def require(self, name: str, nbytes: int) -> bytes:
        import keyring

        v = keyring.get_password(self.SERVICE, name)
        if v is None:
            raise KeyMissing(name)
        key = bytes.fromhex(v)
        if len(key) != nbytes:
            raise ValueError(f"key {name!r} has length {len(key)}, expected {nbytes}")
        return key
