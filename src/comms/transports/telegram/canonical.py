"""TG-JCS-v1, the single copy (spec §9.8 byte contract; comms 5b-3 design §2.3).

Sorted ASCII keys, compact separators, literal UTF-8; floats, lone
surrogates and non-ASCII or non-string keys are fatal (``ValueError``).
Cut verbatim from ``consent/challenge.py`` before consent was removed.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["jcs_dumps"]


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
