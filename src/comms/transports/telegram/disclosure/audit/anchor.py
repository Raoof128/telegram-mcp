"""External audit-head anchor (frozen spec §12.2; design §6.3, §6.7).

A daemon-owned ``0600`` file in a ``0700`` non-database directory — the
spec's reviewed fallback. Its format is frozen because a file two
implementations write differently is not an anchor.

The refresh is durable or it did not happen: write a temp file, fsync it,
rename it over the anchor, then fsync the directory. A refresh that cannot
complete every step reports failure and never reports success.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from comms.core.audit import anchor as _core
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit.profile import (
    ANCHOR_DOMAIN,
    LEGACY_ANCHOR,
    LEGACY_TELEGRAM,
)

__all__ = [
    "ANCHOR_DOMAIN",
    "ANCHOR_PENDING",
    "ANCHOR_VERSION",
    "CLEAN",
    "DEGRADED",
    "FAIL_CLOSED",
    "RECOVERY_REQUIRED",
    "AnchorError",
    "derive_integrity",
    "latch_degraded",
    "read_anchor",
    "repair_anchor",
    "write_anchor",
]

ANCHOR_VERSION = _core.ANCHOR_VERSION

CLEAN = "CLEAN"
ANCHOR_PENDING = "ANCHOR_PENDING"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
DEGRADED = "DEGRADED"
FAIL_CLOSED = "FAIL_CLOSED"

AnchorError = _core.AnchorError


def _anchor_mac(chain_key: bytes, body: dict[str, Any]) -> str:
    return _core.anchor_mac(LEGACY_ANCHOR, chain_key, body)


def write_anchor(
    path: str | Path,
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    event_id: str,
    event_mac: str,
    now: str,
) -> None:
    """Durable refresh: temp write, fsync, rename, fsync the directory."""
    _core.write_anchor(
        LEGACY_ANCHOR,
        path,
        chain_key,
        chain_epoch=chain_epoch,
        chain_seq=chain_seq,
        event_id=event_id,
        event_mac=event_mac,
        now=now,
    )


def read_anchor(path: str | Path, chain_key: bytes) -> dict[str, Any]:
    """Read and authenticate. Every check fails closed, never warns."""
    return _core.read_anchor(LEGACY_ANCHOR, path, chain_key)


def latch_degraded(conn: sqlite3.Connection, *, reason: str, disclosure_ref: str = "") -> None:
    """The single degraded latch (design §6.8). Set only, never cleared here."""
    from comms.transports.telegram.storage.settings import set_setting

    set_setting(conn, "audit.integrity_degraded", 1)
    set_setting(conn, "audit.degraded_disclosure_ref", disclosure_ref)
    set_setting(conn, "audit.degraded_reason", reason)


def derive_integrity(conn: sqlite3.Connection, chain_key: bytes, path: str | Path) -> str:
    """Recompute integrity from the anchor and the chain (design §6.7): the core rule."""
    return _core.derive_integrity(
        conn, LEGACY_TELEGRAM, LEGACY_ANCHOR, lambda _epoch: chain_key, path
    )


def repair_anchor(
    conn: sqlite3.Connection,
    chain_key: bytes,
    checkpoint_key: bytes,
    path: str | Path,
    *,
    now: str,
) -> None:
    """User-presence-gated recovery (design §6.8). The order is load-bearing.

    Appends are forbidden while degraded, so the repair event cannot be
    written first; and a latch cleared before that event is written would
    leave the gap unexplained in the one record built to explain gaps.
    """
    from comms.transports.telegram.disclosure.audit.chain import (
        append_event,
        head,
        mint_event_id,
    )
    from comms.transports.telegram.storage.settings import get_setting, set_setting

    state = derive_integrity(conn, chain_key, path)
    if state != RECOVERY_REQUIRED:
        # Every FAIL CLOSED row is fatal, not repairable: an anchor ahead of
        # the head, a head more than one ahead, or a MAC mismatch is not a
        # stale pointer and is not fixed by moving the pointer.
        raise AnchorError("integrity state is not repairable")

    withheld = get_setting(conn, "audit.degraded_disclosure_ref")
    reason = get_setting(conn, "audit.degraded_reason")

    current = head(conn)  # 1-2 verified above
    if current is None:
        # RECOVERY_REQUIRED implies a head exists, but an invariant the type
        # checker cannot see is one a future edit can break. Fail closed.
        raise AnchorError("cannot repair an empty chain")
    write_anchor(path, chain_key, now=now, **current)  # 3 anchor to the verified head

    with write_tx(conn):  # 4 appends are legal again
        appended = append_event(
            conn,
            chain_key,
            {
                "event_id": mint_event_id(),
                "ts": now,
                "tool_name": "admin.repair_anchor",
                "principal_ref": None,
                "client_ref": None,
                "account_ref": None,
                "peer_ref": None,
                "project_ref": None,
                "project_count": None,
                "policy_epoch": None,
                "result_count": None,
                "duration_ms": None,
                "telegram_rpc_count": None,
                "status": "ok",
                "error_code": reason or None,
                "disclosure_ref": withheld or None,
            },
        )
    write_anchor(
        path,
        chain_key,
        now=now,
        **{  # 5 anchor over that event
            k: appended[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")
        },
    )

    set_setting(conn, "audit.integrity_degraded", 0)  # 6 only now
    set_setting(conn, "audit.degraded_disclosure_ref", "")
    set_setting(conn, "audit.degraded_reason", "")
