"""Loopback bearer leases: the `tgml1` wire, exactly per spec §9.7.1.

Token shape is normative::

    tgml1.<payload_b64url>.<mac_b64url>

``payload_b64url`` is unpadded Base64URL of canonical UTF-8 JSON with
lexicographically sorted keys, no insignificant whitespace, and exactly the
seven keys ``aud``, ``cid``, ``exp``, ``iat``, ``nonce``, ``sec``, ``v``.
``mac_b64url`` is unpadded Base64URL of
``HMAC-SHA-256(key, b"telegram-mcp-local-lease/v1\\0" || ASCII(payload_b64url))``
compared in constant time.

Runtime binding (design §1) is a key-derivation detail, not a wire change:
the MAC key is ``HMAC-SHA-256(client_seed, b"telegram-mcp-lease-runtime/v1\\0"
|| runtime_id)``. A restart mints a new ``runtime_id``, so every previously
minted lease stops verifying while the seven spec fields stay untouched.
``runtime_id=None`` means "not bound to a runtime": it is symmetric, so a
lease minted unbound verifies only against an unbound verifier and vice
versa, and it grants no authority either way.

Rejections, all before any authority is granted: tokens over 1024 ASCII
characters (pre-decode), non-canonical Base64URL (padding, whitespace, any
character outside the alphabet), unknown or duplicate JSON keys, a JSON
boolean where an integer belongs, a nonce not decoding to exactly 16 bytes,
``exp <= iat``, ``exp > iat + 60``, ``v != 1``, an ``aud`` mismatch, clock
skew over 5 seconds, ``sec`` other than the current security epoch, an
unknown ``cid``, and a MAC that is not exactly 32 bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from comms.core.storage.db import write_tx
from comms.core.strict_json import strict_json_loads  # single strict JSON decoder
from comms.transports.telegram.authority.refs import validate_ref_format
from comms.transports.telegram.storage.owner_state import load_security
from comms.transports.telegram.storage.settings import get_setting, put_setting

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "LEASE_AUDIENCE",
    "LEASE_MAX_CHARS",
    "LEASE_MAX_LIFETIME_S",
    "LEASE_MAX_SKEW_S",
    "LEASE_PREFIX",
    "LeaseClaims",
    "LeaseError",
    "mint_lease",
    "revoke_client_auth",
    "verify_lease",
]

LEASE_PREFIX = "tgml1"
LEASE_AUDIENCE = "telegram-mcp-loopback"
LEASE_MAX_CHARS = 1024
LEASE_MAX_LIFETIME_S = 60
LEASE_MAX_SKEW_S = 5
LEASE_NONCE_BYTES = 16

_LEASE_DOMAIN = b"telegram-mcp-local-lease/v1\0"
_RUNTIME_DOMAIN = b"telegram-mcp-lease-runtime/v1\0"
_MAC_BYTES = 32
_KEYS = ("aud", "cid", "exp", "iat", "nonce", "sec", "v")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


class LeaseError(Exception):
    """Lease rejection with a fixed reason; never echoes the token."""


@dataclass(frozen=True)
class LeaseClaims:
    """The seven verified spec fields."""

    aud: str
    client: str
    exp: int
    iat: int
    nonce: str
    security_epoch: int
    version: int


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    if _B64URL_RE.fullmatch(text) is None:
        raise LeaseError("non-canonical base64url")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise LeaseError("non-canonical base64url") from exc


def _mac_key(seed: bytes, runtime_id: bytes | str | None) -> bytes:
    if not isinstance(seed, bytes) or len(seed) < 32:
        raise LeaseError("invalid client seed")
    if runtime_id is None:
        return seed
    raw = bytes.fromhex(runtime_id) if isinstance(runtime_id, str) else bytes(runtime_id)
    if len(raw) != 16:
        raise LeaseError("invalid runtime_id")
    return hmac.new(seed, _RUNTIME_DOMAIN + raw, hashlib.sha256).digest()


def _mac(payload_b64url: str, key: bytes) -> str:
    digest = hmac.new(key, _LEASE_DOMAIN + payload_b64url.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def _int_field(payload: Mapping[str, Any], name: str) -> int:
    value = payload[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise LeaseError(f"{name} must be an integer")
    return value


def mint_lease(
    *,
    seed: bytes,
    client: str,
    epoch: int,
    now: int,
    runtime_id: bytes | str | None = None,
    lifetime_s: int = LEASE_MAX_LIFETIME_S,
    nonce: bytes | None = None,
) -> str:
    """Mint one `tgml1` lease for ``client`` under ``epoch``."""
    try:
        validate_ref_format(client, expect="tcl_")
    except ValueError as exc:
        raise LeaseError("invalid client reference") from exc
    for name, value in (("epoch", epoch), ("now", now), ("lifetime_s", lifetime_s)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise LeaseError(f"{name} must be an integer")
    if not 0 < lifetime_s <= LEASE_MAX_LIFETIME_S:
        raise LeaseError("lifetime exceeds the 60-second maximum")
    raw_nonce = nonce if nonce is not None else secrets.token_bytes(LEASE_NONCE_BYTES)
    if len(raw_nonce) != LEASE_NONCE_BYTES:
        raise LeaseError("nonce must be 16 bytes")
    payload = {
        "aud": LEASE_AUDIENCE,
        "cid": client,
        "exp": now + lifetime_s,
        "iat": now,
        "nonce": _b64url_encode(raw_nonce),
        "sec": epoch,
        "v": 1,
    }
    payload_b64url = _b64url_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    token = f"{LEASE_PREFIX}.{payload_b64url}.{_mac(payload_b64url, _mac_key(seed, runtime_id))}"
    if len(token) > LEASE_MAX_CHARS:
        raise LeaseError("token exceeds 1024 characters")
    return token


def verify_lease(
    token: str,
    *,
    seeds: Mapping[str, bytes],
    epoch: int,
    now: int,
    runtime_id: bytes | str | None = None,
) -> LeaseClaims:
    """Verify a lease in order and return its claims, or raise ``LeaseError``."""
    if not isinstance(token, str):
        raise LeaseError("token must be a string")
    if len(token) > LEASE_MAX_CHARS:
        raise LeaseError("token exceeds 1024 characters")
    if not token.isascii():
        raise LeaseError("token must be ASCII")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != LEASE_PREFIX:
        raise LeaseError("malformed token")
    _, payload_b64url, mac_b64url = parts
    raw = _b64url_decode(payload_b64url)
    mac_raw = _b64url_decode(mac_b64url)
    if len(mac_raw) != _MAC_BYTES:
        raise LeaseError("MAC must be 32 bytes")
    try:
        payload = strict_json_loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LeaseError("payload is not strict canonical JSON") from exc
    if not isinstance(payload, dict) or tuple(sorted(payload)) != _KEYS:
        raise LeaseError("payload keys are not exactly the seven spec keys")

    if _int_field(payload, "v") != 1:
        raise LeaseError("unsupported version")
    if payload["aud"] != LEASE_AUDIENCE:
        raise LeaseError("audience mismatch")
    client = payload["cid"]
    if not isinstance(client, str):
        raise LeaseError("cid must be a string")
    try:
        validate_ref_format(client, expect="tcl_")
    except ValueError as exc:
        raise LeaseError("invalid client reference") from exc
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or len(_b64url_decode(nonce)) != LEASE_NONCE_BYTES:
        raise LeaseError("nonce must decode to exactly 16 bytes")
    iat = _int_field(payload, "iat")
    exp = _int_field(payload, "exp")
    sec = _int_field(payload, "sec")
    if exp <= iat:
        raise LeaseError("exp must be after iat")
    if exp > iat + LEASE_MAX_LIFETIME_S:
        raise LeaseError("lifetime exceeds the 60-second maximum")
    if not isinstance(now, int) or isinstance(now, bool):
        raise LeaseError("now must be an integer")
    if now < iat - LEASE_MAX_SKEW_S:
        raise LeaseError("token is not yet valid")
    if now > exp + LEASE_MAX_SKEW_S:
        raise LeaseError("token has expired")
    if sec != epoch:
        raise LeaseError("security epoch mismatch")

    # cid selects the key slot: a caller cannot pick another identity.
    seed = seeds.get(client)
    if seed is None:
        raise LeaseError("unknown client")
    expected = _mac(payload_b64url, _mac_key(seed, runtime_id))
    if not hmac.compare_digest(expected, mac_b64url):
        raise LeaseError("MAC mismatch")
    return LeaseClaims(
        aud=payload["aud"],
        client=client,
        exp=exp,
        iat=iat,
        nonce=nonce,
        security_epoch=sec,
        version=1,
    )


def revoke_client_auth(conn: sqlite3.Connection, key_dir: Path, *, now: str) -> tuple[int, int]:
    """Retire `tgml1` (comms v0.3 A33): the cutover's ``LEGACY_CLIENT_AUTH_REVOKED`` step.

    One legacy transaction disables every bearer client, bumps the security epoch
    (so no lease already minted verifies) and records ``auth.tgml1_state=revoked``,
    after which the legacy DB refuses to enable or add a bearer client. Then every
    seed file, pending ones included, is deleted. A rerun never bumps the epoch
    again; it only deletes seed files a crash left behind. Returns the number of
    bearer registrations retired and the security epoch, the same on every rerun.
    """
    if get_setting(conn, "auth.tgml1_state") != "revoked":
        with write_tx(conn):
            conn.execute("UPDATE mcp_clients SET enabled = 0 WHERE auth_kind = 'bearer'")
            conn.execute(
                "UPDATE security_state SET security_epoch = security_epoch + 1, updated_at = ?"
                " WHERE singleton_id = 1",
                (now,),
            )
            put_setting(conn, "auth.tgml1_state", "revoked", now=now)
    seeds = sorted(key_dir.glob("lease-seed.*"))
    for path in seeds:
        path.unlink()
    if seeds:
        directory = os.open(key_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    retired = conn.execute("SELECT count(*) FROM mcp_clients WHERE auth_kind = 'bearer'").fetchone()
    return int(retired[0]), load_security(conn)[0]
