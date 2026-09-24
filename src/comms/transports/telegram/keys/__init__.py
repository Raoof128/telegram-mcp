"""Key registry, file store, and daemon-side pairing (Phase 2a, Task 2)."""

from __future__ import annotations

from comms.transports.telegram.keys.pairing import (
    export_public,
    import_peer_pin,
    verify_fingerprint,
)
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
    "export_public",
    "import_peer_pin",
    "key_id",
    "load_key",
    "provision_lease_seed",
    "provision_missing",
    "set_store_dir",
    "verify_fingerprint",
]
