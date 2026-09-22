"""Consent challenge bytes: TG-JCS-v1, digests, daemon/agent signatures.

Frozen **TG-JCS-v1** profile: RFC 8785 restricted to ASCII property names.
``jcs_dumps`` emits ``json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)`` UTF-8 bytes after a strict pre-walk
that rejects any ``float``, any lone surrogate in any string leaf, any
non-ASCII or non-string dict key, and any non-JSON scalar. Control escaping
is stdlib behavior (only U+0000–U+001F plus ``"``/``\\``, lowercase hex);
C1-and-above characters stay literal UTF-8.

Signed challenge set (wire contract, frozen): ``canonical_request_hmac``,
``display_digest``, ``exposure_snapshot_digest``, ``principal``, ``client``,
``account``, ``tool``, ``policy_epoch``, ``project_scope_digest``,
``security_epoch``, ``runtime_id`` (hex), ``nonce`` (16 random bytes →
22-char b64url), ``expiry``. Phase-2 exposure snapshot is the frozen
synthetic-zero form; Phase 3 swaps the computation, never the wire field.

Key roles: Ed25519 (challenge-key seed via ``load_key``) signs daemon
challenges only. Approval signatures are P-256 ECDSA-SHA-256 verified
against the pinned agent key; tests use ``StubSigner`` (deterministic
P-256 fixture via ``ec.derive_private_key``). Production signer lives in
Plan 2b. Fixture seeds (normative for both plans): challenge daemon key
``b"\\x01" * 32``, ``StubSigner`` default seed ``0x07``, vertical-slice
seed ``0x09``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from telegram_mcp.opaque import mint_opaque_ref, validate_ref_format

__all__ = [
    "CHALLENGE_TTL_S",
    "DISPLAY_DOMAIN",
    "FIXTURE_CHALLENGE_KEY",
    "HANDLE_PREFIX",
    "SYNTHETIC_EXPOSURE_SNAPSHOT",
    "StubSigner",
    "canonical_challenge",
    "display_digest",
    "exposure_snapshot_digest",
    "jcs_dumps",
    "mint_challenge_handle",
    "sign_challenge",
    "synthetic_exposure_digest",
    "verify_agent_signature",
    "verify_challenge_signature",
]

# Consent-wait default per plan Global Constraints.
CHALLENGE_TTL_S = 45
DISPLAY_DOMAIN = b"telegram-mcp-display-v1"
HANDLE_PREFIX = "tgu_"

# Fixture daemon challenge key (normative for both plans; Plan 2b byte-equality).
FIXTURE_CHALLENGE_KEY = b"\x01" * 32

# Frozen Phase-2 synthetic-zero exposure snapshot (keys sorted in JCS anyway).
SYNTHETIC_EXPOSURE_SNAPSHOT: dict[str, Any] = {
    "bytes_disclosed": 0,
    "mode": "synthetic",
    "records_disclosed": 0,
    "schema": "tg-mcp-exposure-snapshot/v1",
}

_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_NONCE_BYTES = 16


def _walk_canonicalizable(obj: Any) -> None:
    """Pre-walk: reject float, lone surrogates, non-ASCII/non-string keys."""
    if obj is None or isinstance(obj, bool):
        return
    if isinstance(obj, int):
        return
    if isinstance(obj, float):
        raise ValueError("non-canonical float")  # noqa: TRY004 -- frozen TG-JCS-v1 contract mandates ValueError
    if isinstance(obj, str):
        for char in obj:
            code = ord(char)
            if 0xD800 <= code <= 0xDFFF:
                raise ValueError("lone surrogate")
        try:
            obj.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("lone surrogate") from exc
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ValueError("non-string key")  # noqa: TRY004 -- frozen TG-JCS-v1 contract mandates ValueError
            if not key.isascii():
                raise ValueError("non-ASCII key")
            _walk_canonicalizable(value)
        return
    if isinstance(obj, list):
        for item in obj:
            _walk_canonicalizable(item)
        return
    raise ValueError("non-canonical value")


def jcs_dumps(obj: Any) -> bytes:
    """Dump exact TG-JCS-v1 bytes (sorted keys, compact, literal UTF-8)."""
    _walk_canonicalizable(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def display_digest(payload: Any) -> str:
    """``SHA256("telegram-mcp-display-v1" || JCS(payload))``, lowercase hex."""
    return hashlib.sha256(DISPLAY_DOMAIN + jcs_dumps(payload)).hexdigest()


def exposure_snapshot_digest(snapshot: Any | None = None) -> str:
    """``sha256(JCS(snapshot))``; defaults to the frozen synthetic-zero form."""
    if snapshot is None:
        snapshot = SYNTHETIC_EXPOSURE_SNAPSHOT
    return hashlib.sha256(jcs_dumps(snapshot)).hexdigest()


def synthetic_exposure_digest() -> str:
    """Digest of the frozen Phase-2 synthetic-zero exposure snapshot."""
    return exposure_snapshot_digest(SYNTHETIC_EXPOSURE_SNAPSHOT)


def _new_nonce() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(_NONCE_BYTES)).decode("ascii").rstrip("=")


def _check_hex64(name: str, value: str) -> str:
    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _check_ref(name: str, value: str, prefix: str) -> str:
    if validate_ref_format(value) != prefix:
        raise ValueError(f"invalid {name}")
    return value


def canonical_challenge(
    *,
    tool: str,
    request_hmac: str,
    principal: str,
    client: str,
    account: str,
    policy_epoch: int,
    project_scope_digest: str,
    security_epoch: int,
    runtime_id: bytes | str,
    display_digest: str,
    exposure_snapshot_digest: str,
    nonce: str | None = None,
    expiry: int | None = None,
) -> bytes:
    """Build the exact JCS bytes of the signed challenge object.

    ``nonce``/``expiry`` default to fresh values; pass both explicitly for
    frozen vectors. ``expiry`` defaults to wall-clock ``now + 45`` (agent
    guidance); the broker enforces its own monotonic 45s deadline.
    """
    if not tool or not isinstance(tool, str):
        raise ValueError("invalid tool")
    _check_hex64("request_hmac", request_hmac)
    _check_ref("principal", principal, "prn_")
    _check_ref("client", client, "tcl_")
    _check_ref("account", account, "tga_")
    for name, epoch in (("policy_epoch", policy_epoch), ("security_epoch", security_epoch)):
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise ValueError(f"invalid {name}")
    _check_hex64("project_scope_digest", project_scope_digest)
    _check_hex64("display_digest", display_digest)
    _check_hex64("exposure_snapshot_digest", exposure_snapshot_digest)
    if isinstance(runtime_id, bytes):
        if len(runtime_id) != 16:
            raise ValueError("invalid runtime_id")
        runtime_hex = runtime_id.hex()
    elif isinstance(runtime_id, str):
        if re.fullmatch(r"[0-9a-f]{32}", runtime_id) is None:
            raise ValueError("invalid runtime_id")
        runtime_hex = runtime_id
    else:
        raise ValueError("invalid runtime_id")  # noqa: TRY004 -- uniform ValueError on challenge validation
    if nonce is None:
        nonce = _new_nonce()
    elif not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", nonce):
        raise ValueError("invalid nonce")
    if expiry is None:
        expiry = int(time.time()) + CHALLENGE_TTL_S
    elif not isinstance(expiry, int) or isinstance(expiry, bool) or expiry <= 0:
        raise ValueError("invalid expiry")
    return jcs_dumps(
        {
            "account": account,
            "canonical_request_hmac": request_hmac,
            "client": client,
            "display_digest": display_digest,
            "exposure_snapshot_digest": exposure_snapshot_digest,
            "expiry": expiry,
            "nonce": nonce,
            "policy_epoch": policy_epoch,
            "principal": principal,
            "project_scope_digest": project_scope_digest,
            "runtime_id": runtime_hex,
            "security_epoch": security_epoch,
            "tool": tool,
        }
    )


def _b64url_nopad_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_nopad_decode(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("invalid base64url")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid base64url") from exc


def sign_challenge(raw: bytes, challenge_key: bytes) -> str:
    """Daemon Ed25519 signs raw JCS bytes; base64url (no pad) signature."""
    if not isinstance(challenge_key, bytes) or len(challenge_key) != 32:
        raise ValueError("invalid challenge key")
    private = Ed25519PrivateKey.from_private_bytes(challenge_key)
    return _b64url_nopad_encode(private.sign(raw))


def verify_challenge_signature(raw: bytes, sig: str, public_bytes: bytes) -> bool:
    """Verify a daemon Ed25519 challenge signature (constant-shape, no raise)."""
    try:
        public = Ed25519PublicKey.from_public_bytes(public_bytes)
        public.verify(_b64url_nopad_decode(sig), raw)
    except (InvalidSignature, ValueError):
        return False
    return True


def verify_agent_signature(sig: bytes, msg: bytes, public_bytes: bytes) -> bool:
    """Verify a P-256 ECDSA-SHA-256 DER agent signature (never raises)."""
    try:
        key = serialization.load_der_public_key(public_bytes)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            return False
        key.verify(sig, msg, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        return False
    return True


def mint_challenge_handle() -> str:
    """Mint a ``tgu_`` opaque handle (minter lives in ``opaque.py``)."""
    return mint_opaque_ref(HANDLE_PREFIX)


class StubSigner:
    """Test-only deterministic P-256 signer/verify pair (never production).

    Keypair via ``ec.derive_private_key(seed, ec.SECP256R1)``; ``key_id`` is
    the ``p256:sha256:`` fingerprint of the DER SPKI. ``sign`` is
    ECDSA-SHA-256 DER (random-k per signature is fine — verifiability plus
    the pinned public key is what's asserted). ``verify(sig, msg)`` is a
    bound two-argument closure over the implicit fixture public key;
    production passes a closure over the pinned agent key with the same
    shape.
    """

    def __init__(self, seed: int = 0x07) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("invalid stub seed")
        self._private = ec.derive_private_key(seed, ec.SECP256R1())
        public = self._private.public_key()
        self.public_bytes: bytes = public.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.key_id: str = "p256:sha256:" + hashlib.sha256(self.public_bytes).hexdigest()

    def sign(self, msg: bytes) -> bytes:
        """ECDSA-SHA-256 over ``msg``; DER signature bytes."""
        return self._private.sign(bytes(msg), ec.ECDSA(hashes.SHA256()))

    def verify(self, sig: bytes, msg: bytes) -> bool:
        """Bound two-argument verifier over the fixture public key."""
        return verify_agent_signature(bytes(sig), bytes(msg), self.public_bytes)
