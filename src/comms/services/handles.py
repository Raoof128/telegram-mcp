"""Client-bound context handles and MACed, key-versioned cursors (comms v0.3 Task D9; A30, G13).

A ``ctx_`` handle holds a context query's snapshot server-side, bound to the client that opened
it, the security epoch it was made in, and an expiry. A cursor token is
``cur_<ref>.<mac>``: HMAC-SHA256 under the cursor key version that minted it over
``b"comms-cursor/v1\\0" + JCS({ref, ctx_ref, client, epoch})``, truncated to 32 hex characters.
Any other client, another epoch, an expiry, a rotated or destroyed key version, or a MAC that
does not verify is ``STALE_HANDLE``; neither a handle nor a cursor is ever a write target.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from comms.core import context_handles as rows
from comms.core import domains, refs, timeutil
from comms.core.canonical import jcs_dumps
from comms.core.errors import CommsError
from comms.core.keys.slots import KeySlotError, KeySlotStore, active_version, load_version
from comms.core.security import security_epoch

__all__ = ["HANDLE_TTL", "ContextHandles"]

HANDLE_TTL = timedelta(hours=1)
MAC_HEX = 32


class ContextHandles:
    def __init__(self, conn: Any, store: KeySlotStore, *, clock: Callable[[], datetime]) -> None:
        self._conn, self._store, self._clock = conn, store, clock

    def __repr__(self) -> str:
        return "ContextHandles(<redacted>)"

    def open(
        self,
        *,
        client: str,
        owner: str,
        target_ref: str,
        actor: str,
        query_digest: str,
        snapshot: Mapping[str, Any],
    ) -> str:
        now = timeutil.utc(self._clock())
        return rows.insert_handle(
            self._conn,
            client=client,
            owner=owner,
            epoch=security_epoch(self._conn),
            query_digest=query_digest,
            target_ref=target_ref,
            actor=actor,
            snapshot=dict(snapshot),
            created_at=timeutil.iso(now),
            expires_at=timeutil.iso(now + HANDLE_TTL),
        )

    def resume(self, client: str, ref: str) -> rows.HandleRow:
        try:
            refs.check(ref, "context")
        except ValueError:
            raise CommsError("INVALID_ARGUMENT") from None
        handle = rows.load_handle(self._conn, ref)
        if (
            handle is None
            or not hmac.compare_digest(handle.client, client)
            or self._stale(handle.security_epoch, handle.expires_at)
        ):
            raise CommsError("STALE_HANDLE")
        return handle

    def cursor(self, client: str, ctx_ref: str, position: Mapping[str, Any]) -> str:
        handle = self.resume(client, ctx_ref)
        row = active_version(self._conn, "cursor-key")
        if row is None:
            raise CommsError("NOT_CONFIGURED")
        version = int(row[0])
        now = timeutil.utc(self._clock())
        ref = rows.insert_cursor(
            self._conn,
            ctx_ref=handle.ref,
            position=dict(position),
            key_version=version,
            created_at=timeutil.iso(now),
            expires_at=handle.expires_at,
        )
        return f"{ref}.{self._mac(version, ref, handle.ref, client, handle.security_epoch)}"

    def position(self, client: str, token: str) -> tuple[rows.HandleRow, dict[str, Any]]:
        ref, sep, mac = token.partition(".") if isinstance(token, str) else ("", "", "")
        try:
            refs.check(ref, "cursor")
        except ValueError:
            raise CommsError("INVALID_ARGUMENT") from None
        cursor = rows.load_cursor(self._conn, ref)
        if not sep or cursor is None:
            raise CommsError("STALE_HANDLE")
        handle = rows.load_handle(self._conn, cursor.ctx_ref)
        if handle is None or self._stale(handle.security_epoch, cursor.expires_at):
            raise CommsError("STALE_HANDLE")
        try:
            expected = self._mac(
                cursor.key_version, ref, cursor.ctx_ref, client, handle.security_epoch
            )
        except KeySlotError:
            raise CommsError("STALE_HANDLE") from None  # a destroyed or rotated key version
        if not hmac.compare_digest(mac, expected) or not hmac.compare_digest(handle.client, client):
            raise CommsError("STALE_HANDLE")
        return handle, cursor.position

    def _stale(self, epoch: int, expires_at: str) -> bool:
        now = timeutil.utc(self._clock())
        return epoch != security_epoch(self._conn) or now >= timeutil.instant(expires_at)

    def _mac(self, version: int, ref: str, ctx_ref: str, client: str, epoch: int) -> str:
        key = load_version(self._conn, self._store, "cursor-key", version)
        body = jcs_dumps({"client": client, "ctx_ref": ctx_ref, "epoch": epoch, "ref": ref})
        return hmac.new(key, domains.CURSOR + body, hashlib.sha256).hexdigest()[:MAC_HEX]
