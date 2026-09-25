"""``cml1`` local leases (comms v0.3 Task D27; A33, G17).

``cml1.<b64url(JCS payload)>.<b64url(HMAC-SHA256(seed, "comms-local-lease/v1\\0" ‖ payload))>``
with payload exactly ``{aud: "comms-loopback", cid, exp, iat, nonce, sec, v: 1}``.

``verify`` refuses, with one fixed message, anything but: a token of at most 1 KiB from a
loopback socket or the admin peer credential; a payload that is strict JSON (no duplicate
keys), canonical, and has exactly those keys; a lifetime of at most 60 s and a clock within
30 s of it; a 16-byte nonce; a ``cid`` naming an enabled client whose **current** seed MACs
the payload (compared in constant time); and ``sec`` equal to the current security epoch.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any

from comms.core import refs
from comms.core.auth.clients import client_seed
from comms.core.auth.lease_format import (
    AUDIENCE,
    MAX_LIFETIME_S,
    NONCE_BYTES,
    PREFIX,
    decode,
    mac,
    mint,
)
from comms.core.canonical import jcs_dumps
from comms.core.keys.slots import KeySlotStore
from comms.core.security import security_epoch
from comms.core.strict_json import strict_json_loads

__all__ = ["AUDIENCE", "LeaseRefused", "mint", "verify"]

MAX_TOKEN = 1024
SKEW_S = 30
SOURCES = frozenset({"loopback", "admin_peer"})
_KEYS = frozenset({"aud", "cid", "exp", "iat", "nonce", "sec", "v"})


class LeaseRefused(Exception):
    """A lease was refused. One fixed message: nothing says which check failed."""

    def __init__(self) -> None:
        super().__init__("lease refused")


def _decode(text: str) -> bytes:
    try:
        return decode(text)
    except ValueError:
        raise LeaseRefused from None


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise LeaseRefused
    return value


def verify(conn: Any, store: KeySlotStore, token: str, *, now: datetime, source: str) -> str:
    """The lease's client ref, or ``LeaseRefused``."""
    if source not in SOURCES:
        raise LeaseRefused
    if not isinstance(token, str) or len(token) > MAX_TOKEN:
        raise LeaseRefused
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LeaseRefused
    payload, given = _decode(parts[1]), _decode(parts[2])
    try:
        claims = strict_json_loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LeaseRefused from None
    if not isinstance(claims, dict) or set(claims) != _KEYS or jcs_dumps(claims) != payload:
        raise LeaseRefused
    cid = claims["cid"]
    try:
        refs.check(cid, "client")
    except ValueError:
        raise LeaseRefused from None
    seed = client_seed(conn, store, cid)
    if seed is None or not hmac.compare_digest(mac(seed, payload), given):
        raise LeaseRefused
    iat, exp, clock = _integer(claims["iat"]), _integer(claims["exp"]), int(now.timestamp())
    if claims["aud"] != AUDIENCE or claims["v"] != 1 or type(claims["v"]) is not int:
        raise LeaseRefused
    if not 0 < exp - iat <= MAX_LIFETIME_S or not iat - SKEW_S <= clock <= exp + SKEW_S:
        raise LeaseRefused
    nonce = claims["nonce"]
    if not isinstance(nonce, str) or len(_decode(nonce)) != NONCE_BYTES:
        raise LeaseRefused
    if _integer(claims["sec"]) != security_epoch(conn):
        raise LeaseRefused
    return cid
