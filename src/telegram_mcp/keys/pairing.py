"""Daemon-side pairing pins: export/import publics, fingerprint compare.

The daemon never holds agent private keys. It stores pinned publics (the
agent approval P-256 public and the agent transport Ed25519 public under
separate pin slots) plus fingerprints for on-screen comparison. The daemon
challenge pin reaches the agent via the keychain pairing record in Plan 2b;
this module handles the daemon side only.

Pin files (``<name>.pin``) live in the bound store directory, written
``0600``. Fingerprints are recomputed from stored bytes on every verify —
never trusted from disk.
"""

from __future__ import annotations

import hmac as hmac_compare
import os
import tempfile
from pathlib import Path

from telegram_mcp.keys import store as _store
from telegram_mcp.keys.registry import KEY_REGISTRY

__all__ = ["export_public", "import_peer_pin", "verify_fingerprint"]


def _pin_path(name: str) -> Path:
    if name not in KEY_REGISTRY:
        raise _store.KeyStoreError(f"{_store.ERR_UNKNOWN}: {name}")
    return _store.get_store_dir() / f"{name}.pin"


def export_public(name: str) -> bytes:
    """Return the public half for ``name``: pinned peer key or derived local."""
    if name not in KEY_REGISTRY:
        raise _store.KeyStoreError(f"{_store.ERR_UNKNOWN}: {name}")
    pin = _store.get_store_dir() / f"{name}.pin"
    if pin.exists():
        return pin.read_bytes()
    spec = KEY_REGISTRY[name]
    if spec.algorithm == "Ed25519":
        return _store._ed25519_public_halves(_store.load_key(name))
    raise _store.KeyStoreError(f"{_store.ERR_MISSING}: {name}")


def import_peer_pin(name: str, public_bytes: bytes) -> str:
    """Store a peer public + return its fingerprint for on-screen compare.

    For the agent this is called twice: once for the approval P-256 public
    (``agent-approval-key``) and once for the transport Ed25519 public
    (``agent-transport-key``) — separate pin slots, separate fingerprints.
    Re-pairing overwrites the slot (rotation history lives in Task 8).
    """
    pin = _pin_path(name)
    fingerprint = _store.fingerprint_for(name, bytes(public_bytes))
    fd, tmp_name = tempfile.mkstemp(dir=str(pin.parent), prefix=f"{name}.pin.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(bytes(public_bytes))
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, pin)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return fingerprint


def verify_fingerprint(name: str, shown: str) -> bool:
    """Constant-time compare of ``shown`` against the recomputed fingerprint."""
    try:
        pin = _pin_path(name)
        if pin.exists():
            expected = _store.fingerprint_for(name, pin.read_bytes())
        else:
            expected = _store.key_id(name)
        return hmac_compare.compare_digest(expected, shown)
    except (_store.KeyStoreError, TypeError):
        return False
