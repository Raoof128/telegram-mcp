"""``auth headers``: the credential helper (spec §8.2.1, §9.7.1).

A lease authenticates a client for at most 60 seconds. It is not authority:
every sensitive call still meets the project grants and the budget. The seed is read, never
minted, so an unknown client gets nothing.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from comms.transports.telegram.ipc.leases import mint_lease
from comms.transports.telegram.runtime.identity import resolve_principal
from comms.transports.telegram.storage.authority_view import load_security

__all__ = ["auth_headers_handler"]


def auth_headers_handler(
    conn: sqlite3.Connection,
    *,
    seed_for: Callable[[str], bytes | None],
    runtime_id: bytes,
    clock: Callable[[], float] = time.time,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        client_ref = args.get("client_ref")
        if not isinstance(client_ref, str) or resolve_principal(conn, client_ref) is None:
            raise PermissionError("unknown or disabled client")
        seed = seed_for(client_ref)
        if seed is None:
            raise PermissionError("client has no credential")
        epoch, _locked = load_security(conn)
        token = mint_lease(
            seed=seed, client=client_ref, epoch=epoch, now=int(clock()), runtime_id=runtime_id
        )
        return {"authorization": f"Bearer {token}", "expires_in": 60}

    return handler
