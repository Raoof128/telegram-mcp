"""Typed, finite-domain field validators: the one copy (5b-4 S6; comms v0.3 A7 typed audit events).

A payload is accepted only when every key has a validator and every value passes it.
There is no free-form string bucket.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from comms.core import refs, timeutil
from comms.core.opaque import validate_ref_format

__all__ = [
    "Validator",
    "count",
    "digest",
    "key_id",
    "nullable",
    "one_of",
    "opaque_ref",
    "ref",
    "time",
]

Validator = Callable[[Any], bool]
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"(hmac|ed25519):sha256:[0-9a-f]{64}\Z")


def ref(kind: str) -> Validator:
    def check(value: Any) -> bool:
        try:
            refs.check(value, kind)
        except ValueError:
            return False
        return True

    return check


def one_of(values: frozenset[str] | set[str]) -> Validator:
    return lambda value: isinstance(value, str) and value in values


def count(minimum: int) -> Validator:
    return lambda value: type(value) is int and value >= minimum


def digest(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def key_id(value: Any) -> bool:
    return isinstance(value, str) and _KEY_ID.fullmatch(value) is not None


def time(value: Any) -> bool:
    try:
        timeutil.parse(value)
    except ValueError:
        return False
    return True


def nullable(validator: Validator) -> Validator:
    return lambda value: value is None or validator(value)


def opaque_ref(value: Any) -> bool:
    """Any ``<prefix>_<26 base32>`` ref, whatever its kind (e.g. a legacy checkpoint ref)."""
    try:
        validate_ref_format(value)
    except (TypeError, ValueError):
        return False
    return True
