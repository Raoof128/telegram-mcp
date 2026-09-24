"""Key registry and file store (Phase 2a, Task 2)."""

from __future__ import annotations

from comms.transports.telegram.keys.registry import KEY_REGISTRY, KeySpec
from comms.transports.telegram.keys.store import (
    KeyStoreError,
    key_id,
    load_key,
    provision_lease_seed,
    provision_missing,
    set_store_dir,
)

__all__ = [
    "KEY_REGISTRY",
    "KeySpec",
    "KeyStoreError",
    "key_id",
    "load_key",
    "provision_lease_seed",
    "provision_missing",
    "set_store_dir",
]
