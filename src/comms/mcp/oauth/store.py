"""In-memory, short-lived OAuth state (comms v0.3 Task D32; A35).

Owner approvals are one-time codes that ``comms oauth approve`` prints locally, valid for five
minutes. Authorization codes are 32 random bytes, valid 60 s, spent once. Both are held only as
SHA-256 digests; neither survives a restart, which only shortens their already short lives.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

__all__ = ["APPROVAL_TTL", "CODE_TTL", "CodeStore", "OwnerApprovals"]

APPROVAL_TTL = timedelta(minutes=5)
CODE_TTL = timedelta(seconds=60)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8", "replace")).hexdigest()


class OwnerApprovals:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._pending: dict[str, datetime] = {}

    def __repr__(self) -> str:
        return "OwnerApprovals(<redacted>)"

    def issue(self) -> str:
        code = base64.b32encode(os.urandom(10)).decode("ascii").rstrip("=")
        self._pending[_digest(code)] = self._clock() + APPROVAL_TTL
        return code

    def consume(self, code: object) -> bool:
        if not isinstance(code, str) or not code:
            return False
        now, wanted = self._clock(), _digest(code)
        self._pending = {d: e for d, e in self._pending.items() if e > now}
        for digest in list(self._pending):
            if hmac.compare_digest(digest, wanted):
                del self._pending[digest]
                return True
        return False


class CodeStore:
    """Authorization codes by digest: each spent at most once, gone after 60 s."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._codes: dict[str, tuple[datetime, Any]] = {}

    def __repr__(self) -> str:
        return "CodeStore(<redacted>)"

    def put(self, code: str, value: Any) -> None:
        self._codes[_digest(code)] = (self._clock() + CODE_TTL, value)

    def get(self, code: str) -> Any | None:
        entry = self._codes.get(_digest(code)) if isinstance(code, str) else None
        if entry is None or entry[0] <= self._clock():
            return None
        return entry[1]

    def spend(self, code: str) -> Any | None:
        value = self.get(code)
        self._codes.pop(_digest(code), None)
        return value
