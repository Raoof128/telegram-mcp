"""Neutral opaque-reference minter/validator (single copy).

Both the consent broker (``tgu_`` handles, Task 3) and the authority refs
(``tpr_``/``tcl_``/…, Task 6) import from this module — no second copy may
exist anywhere. Rule: ``prefix + 26 chars [a-z2-7]`` from 130 CSPRNG bits,
non-sequential.
"""

from __future__ import annotations

import base64
import re
import secrets

__all__ = ["mint_opaque_ref", "validate_ref_format"]

# Lowercase unpadded Base32 body: exactly 26 chars = 130 bits.
_BODY_RE = re.compile(r"[a-z2-7]{26}\Z")
# Prefixes are lowercase alpha segments ending in "_", e.g. "tgu_", "tpr_".
_REF_RE = re.compile(r"([a-z]+_)[a-z2-7]{26}\Z")


def mint_opaque_ref(prefix: str) -> str:
    """Mint ``prefix + 26`` lowercase unpadded Base32 chars (130 CSPRNG bits)."""
    if not prefix.endswith("_") or not prefix[:-1].isalpha() or not prefix[:-1].islower():
        raise ValueError("invalid opaque-ref prefix")
    raw = secrets.randbits(130).to_bytes(17, "big")
    body = base64.b32encode(raw).decode("ascii")[:26].lower()
    assert _BODY_RE.fullmatch(body) is not None
    return prefix + body


def validate_ref_format(ref: str) -> str:
    """Validate shape; return the prefix (e.g. ``"tgu_"``) or raise ``ValueError``."""
    match = _REF_RE.fullmatch(ref)
    if match is None:
        raise ValueError("invalid opaque-ref format")
    return match.group(1)
