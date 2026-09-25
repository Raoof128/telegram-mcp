"""The ``cml1`` token format and the client helper file, with no privileged imports
(comms v0.3 Tasks D27, D29; A33, A34).

Pure encoding: the unprivileged stdio proxy mints leases with this module and nothing else of
the daemon's; ``leases.verify`` checks them against the daemon's seed copy. The helper file is
``{"client": "cli_…", "seed": "<b64url 32 bytes>", "v": 1}`` (canonical JSON), 0600, owned by
the reader; a file anyone else may read is refused.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import stat
from datetime import datetime
from pathlib import Path

from comms.core import domains
from comms.core.canonical import jcs_dumps
from comms.core.strict_json import strict_json_loads

__all__ = [
    "AUDIENCE",
    "MAX_LIFETIME_S",
    "NONCE_BYTES",
    "PREFIX",
    "HelperFileError",
    "decode",
    "encode",
    "helper_bytes",
    "mac",
    "mint",
    "read_helper",
]

PREFIX = "cml1"
AUDIENCE = domains.LOCAL_LEASE_AUDIENCE
MAX_LIFETIME_S = 60
NONCE_BYTES = 16
SEED_BYTES = 32


class HelperFileError(Exception):
    """The helper file was refused. Fixed messages, never its content."""


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode(text: str) -> bytes:
    """One encoding only: unpadded base64url that re-encodes to itself, else ``ValueError``."""
    if not isinstance(text, str) or not text or not text.isascii():
        raise ValueError("not base64url")
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("not base64url") from None
    if encode(data) != text:
        raise ValueError("not base64url")
    return data


def mac(seed: bytes, payload: bytes) -> bytes:
    return hmac.new(seed, domains.LOCAL_LEASE + payload, hashlib.sha256).digest()


def mint(seed: bytes, cid: str, sec: int, *, now: datetime, lifetime: int = MAX_LIFETIME_S) -> str:
    """A fresh lease: its own nonce, ``iat`` now, ``exp`` at most 60 s later."""
    iat = int(now.timestamp())
    payload = jcs_dumps(
        {
            "aud": AUDIENCE,
            "cid": cid,
            "exp": iat + min(lifetime, MAX_LIFETIME_S),
            "iat": iat,
            "nonce": encode(os.urandom(NONCE_BYTES)),
            "sec": sec,
            "v": 1,
        }
    )
    return f"{PREFIX}.{encode(payload)}.{encode(mac(seed, payload))}"


def helper_bytes(cid: str, seed: bytes) -> bytes:
    return jcs_dumps({"client": cid, "seed": encode(seed), "v": 1})


def read_helper(path: Path) -> tuple[str, bytes]:
    """``(client ref, seed)`` from a 0600 helper file this user owns; anything else refused."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise HelperFileError("the client seed file cannot be read") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise HelperFileError("the client seed file is not this user's regular file")
        if info.st_mode & 0o077:
            raise HelperFileError("the client seed file is readable by others")
        raw = os.read(fd, 4096)
    finally:
        os.close(fd)
    try:
        parsed = strict_json_loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict) or set(parsed) != {"client", "seed", "v"}:
            raise ValueError("shape")
        if parsed["v"] != 1 or not isinstance(parsed["client"], str):
            raise ValueError("shape")
        seed = decode(parsed["seed"])
    except (UnicodeDecodeError, ValueError):
        raise HelperFileError("the client seed file is malformed") from None
    if len(seed) != SEED_BYTES:
        raise HelperFileError("the client seed file is malformed")
    return parsed["client"], seed
