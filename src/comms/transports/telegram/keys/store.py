"""File-backed key store: provision, load (``0600``-enforced), fingerprints.

Private material lives in ``0600`` files inside a ``0700`` directory owned by
the current euid. Key IDs are recomputed on load, never stored. This module
never logs key material; every failure raises ``KeyStoreError`` with a fixed
string (the key name may be appended — never the material).

Binding: the daemon binds one store directory per start (``set_store_dir``;
``provision_missing`` binds as a side effect). ``load_key``/``key_id`` use
the bound directory so their signatures stay exactly ``(name)``.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core.keys import ids
from comms.transports.telegram.keys.registry import KEY_REGISTRY, KeySpec

__all__ = [
    "FILE_BACKED_KEYS",
    "KeyStoreError",
    "cached_fingerprint",
    "fingerprint_for",
    "get_store_dir",
    "key_id",
    "load_key",
    "provision_lease_seed",
    "provision_missing",
    "read_lease_seed",
    "set_store_dir",
]

# Fixed error strings. Key names may be appended after ": "; key material
# must never appear in any message.
ERR_NOT_CONFIGURED = "key store directory is not configured"
ERR_STORE_DIR = "key store directory must be mode 0700 owned by the current user"
ERR_UNKNOWN = "unknown key purpose"
ERR_MISSING = "key file is missing"
ERR_PERMS = "key file permissions must be 0600"
ERR_MATERIAL = "invalid key material"
ERR_NO_PUBLIC = "key has no public half"
ERR_CLIENT = "invalid client reference"

# Rows provisioned as private files by ``provision_missing``: the Phase-2
# file-backed runtime-account secrets. Tunnel references come from install,
# per-client lease seeds are minted on demand via ``provision_lease_seed``,
# and the retired consent rows (comms spec v0.2) are never provisioned.
_FILE_BACKED_ROWS = frozenset(
    {
        "principal-key",
        "cursor-key",
        "privacy-key",
        # Phase 3: the rows that sign proofs, sign chain heads and MAC events.
        "disclosure-key",
        "audit-checkpoint-key",
        "audit-chain-key",
    }
)
# Public alias: doctor checks exactly these rows as runtime-owned files.
FILE_BACKED_KEYS = _FILE_BACKED_ROWS

_SEED_LEN = 32
_CLIENT_REF_RE = re.compile(r"[A-Za-z0-9_\-]{1,128}\Z")

_STORE_DIR: Path | None = None
_FINGERPRINT_CACHE: dict[str, str] = {}


class KeyStoreError(Exception):
    """Any key-store failure. Fixed message; never carries key material."""


def _spec(name: str) -> KeySpec:
    spec = KEY_REGISTRY.get(name)
    if spec is None:
        raise KeyStoreError(f"{ERR_UNKNOWN}: {name}")
    return spec


def _ensure_store_dir(path: str | Path) -> Path:
    """Create-if-missing, then verify ``0700`` + current-euid ownership.

    Existing directories are only verified, never chmod'd: permission repair
    outside tmp fixtures is the installer's job, not the daemon's.
    """
    root = Path(path)
    if not root.exists():
        root.mkdir(mode=0o700, parents=True)
        os.chmod(root, 0o700)  # mkdir mode is umask-masked; pin ours exactly
    if not root.is_dir():
        raise KeyStoreError(ERR_STORE_DIR)
    st = root.stat()
    if stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.geteuid():
        raise KeyStoreError(ERR_STORE_DIR)
    return root


def set_store_dir(path: str | Path) -> Path:
    """Bind the process-wide store directory (once per start)."""
    global _STORE_DIR
    _STORE_DIR = _ensure_store_dir(path)
    return _STORE_DIR


def get_store_dir() -> Path:
    """Return the bound store directory, re-verifying it on every access."""
    if _STORE_DIR is None:
        raise KeyStoreError(ERR_NOT_CONFIGURED)
    return _ensure_store_dir(_STORE_DIR)


def _atomic_write_0600(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _new_private_material(spec: KeySpec) -> bytes:
    if spec.algorithm == "HMAC-SHA-256":
        return secrets.token_bytes(_SEED_LEN)
    if spec.algorithm == "Ed25519":
        return Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    raise KeyStoreError(ERR_NO_PUBLIC)


def provision_missing(store_dir: str | Path, *, phases: tuple[int, ...] = (2,)) -> list[str]:
    """Create missing private files for Phase-2 file-backed rows only.

    Existing files are never overwritten. Binds ``store_dir`` for later
    ``load_key``/``key_id`` calls. Returns the names actually created.
    """
    wanted = set(phases)
    root = set_store_dir(store_dir)
    created: list[str] = []
    for name in KEY_REGISTRY:
        spec = KEY_REGISTRY[name]
        if spec.retired or spec.required_phase not in wanted or name not in _FILE_BACKED_ROWS:
            continue
        dest = root / name
        if dest.exists():
            continue
        _atomic_write_0600(dest, _new_private_material(spec))
        created.append(name)
    return created


def _ed25519_public_halves(seed: bytes) -> bytes:
    try:
        private = Ed25519PrivateKey.from_private_bytes(seed)
    except (ValueError, InvalidKey) as exc:
        raise KeyStoreError(ERR_MATERIAL) from exc
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def load_key(name: str) -> bytes:
    """Load private material for ``name``; verifies ``0600`` + recomputes ID.

    The recomputed fingerprint is cached for the ``recompute_key_ids``
    startup step (see ``cached_fingerprint``).
    """
    spec = _spec(name)
    root = get_store_dir()
    path = root / name
    try:
        st = path.stat()
    except FileNotFoundError as exc:
        raise KeyStoreError(f"{ERR_MISSING}: {name}") from exc
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise KeyStoreError(f"{ERR_PERMS}: {name}")
    data = path.read_bytes()
    if spec.algorithm == "HMAC-SHA-256":
        if len(data) != _SEED_LEN:
            raise KeyStoreError(f"{ERR_MATERIAL}: {name}")
        _FINGERPRINT_CACHE[name] = ids.hmac_key_id(data)
    elif spec.algorithm == "Ed25519":
        if len(data) != _SEED_LEN:
            raise KeyStoreError(f"{ERR_MATERIAL}: {name}")
        _FINGERPRINT_CACHE[name] = ids.ed25519_key_id(_ed25519_public_halves(data))
    else:
        # Reference/device-bound rows have no private file here.
        raise KeyStoreError(f"{ERR_MISSING}: {name}")
    return data


def _extract_spki(public_bytes: bytes) -> bytes:
    """Return DER SPKI for DER-cert or DER-SPKI input."""
    try:
        cert = x509.load_der_x509_certificate(public_bytes)
    except ValueError:
        pass
    else:
        return cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    try:
        key = serialization.load_der_public_key(public_bytes)
    except ValueError as exc:
        raise KeyStoreError(ERR_MATERIAL) from exc
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def fingerprint_for(name: str, public_bytes: bytes) -> str:
    """Compute the ``<kind>:sha256:<hex>`` fingerprint for pinned public bytes."""
    spec = _spec(name)
    algorithm = spec.algorithm
    if algorithm.startswith("Ed25519"):
        if len(public_bytes) != _SEED_LEN:
            raise KeyStoreError(f"{ERR_MATERIAL}: {name}")
        return ids.ed25519_key_id(public_bytes)
    if algorithm == "P-256 Secure Enclave":
        try:
            key = serialization.load_der_public_key(public_bytes)
        except ValueError as exc:
            raise KeyStoreError(f"{ERR_MATERIAL}: {name}") from exc
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise KeyStoreError(f"{ERR_MATERIAL}: {name}")
        return "p256:sha256:" + hashlib.sha256(public_bytes).hexdigest()
    if algorithm == "X.509/SPKI":
        try:
            spki = _extract_spki(public_bytes)
        except KeyStoreError as exc:
            raise KeyStoreError(f"{ERR_MATERIAL}: {name}") from exc
        return "spki:sha256:" + hashlib.sha256(spki).hexdigest()
    raise KeyStoreError(f"{ERR_NO_PUBLIC}: {name}")


def key_id(name: str) -> str:
    """Recompute the key fingerprint for ``name`` (never stored)."""
    spec = _spec(name)
    root = get_store_dir()
    pin = root / (name + ".pin")
    if pin.exists():
        return fingerprint_for(name, pin.read_bytes())
    if spec.algorithm in ("HMAC-SHA-256", "Ed25519"):
        load_key(name)  # verifies 0600, recomputes + caches the fingerprint
        return _FINGERPRINT_CACHE[name]
    raise KeyStoreError(f"{ERR_MISSING}: {name}")


def cached_fingerprint(name: str) -> str | None:
    """Last fingerprint recomputed by ``load_key`` (startup step 6 helper)."""
    _spec(name)
    return _FINGERPRINT_CACHE.get(name)


def provision_lease_seed(store_dir: str | Path, client_ref: str) -> bytes:
    """Mint-or-load the 32-byte lease seed for one client (``0600`` file)."""
    if not _CLIENT_REF_RE.fullmatch(client_ref):
        raise KeyStoreError(f"{ERR_CLIENT}: {client_ref}")
    root = set_store_dir(store_dir)
    path = root / f"lease-seed.{client_ref}"
    if not path.exists():
        _atomic_write_0600(path, secrets.token_bytes(_SEED_LEN))
    st = path.stat()
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise KeyStoreError(f"{ERR_PERMS}: {path.name}")
    data = path.read_bytes()
    if len(data) != _SEED_LEN:
        raise KeyStoreError(f"{ERR_MATERIAL}: {path.name}")
    return data


def read_lease_seed(store_dir: str | Path, client_ref: str) -> bytes | None:
    """Read one client's lease seed; never mint. ``None`` when absent or unsafe.

    The ingress verifies bearers with this. ``provision_lease_seed`` mints
    on a miss, which would let an unknown ``cid`` create its own key.
    """
    if not _CLIENT_REF_RE.fullmatch(client_ref):
        return None
    path = Path(store_dir) / f"lease-seed.{client_ref}"
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    if stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.geteuid():
        return None
    data = path.read_bytes()
    return data if len(data) == _SEED_LEN else None
