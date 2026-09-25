"""``admin.identity.inspect`` (comms v0.3 Task D17; P §44).

The one service that returns provider identities — delivery identities, chat ids, provider
object ids — and only for a ref the owner names explicitly. Every other service returns refs.
The catalog annotates it read-only and owner-only (D24).
"""

from __future__ import annotations

from typing import Any

from comms.core.identities import identities_of
from comms.services.local import mapped

__all__ = ["IdentityService"]


class IdentityService:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def inspect(self, ref: str) -> dict[str, Any]:
        return {"ref": ref, "identities": mapped(lambda: identities_of(self._conn, ref))}
