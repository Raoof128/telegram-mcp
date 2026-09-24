"""External audit-head anchor (frozen spec §12.2; design §6.3, §6.7).

A daemon-owned ``0600`` file in a ``0700`` non-database directory — the
spec's reviewed fallback. Its format is frozen because a file two
implementations write differently is not an anchor.

The refresh is durable or it did not happen: write a temp file, fsync it,
rename it over the anchor, then fsync the directory. A refresh that cannot
complete every step reports failure and never reports success.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Any

from comms.core.canonical import jcs_dumps
from comms.transports.telegram.disclosure.audit.chain import ChainError, head, verify_chain

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

ANCHOR_DOMAIN = b"telegram-mcp-anchor-v1"
ANCHOR_VERSION = 1

CLEAN = "CLEAN"
ANCHOR_PENDING = "ANCHOR_PENDING"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
DEGRADED = "DEGRADED"
FAIL_CLOSED = "FAIL_CLOSED"


class AnchorError(Exception):
    """The anchor is unreadable, unauthenticated or out of contract."""


def _anchor_mac(chain_key: bytes, body: dict[str, Any]) -> str:
    without = {k: v for k, v in body.items() if k != "anchor_mac"}
    return hmac.new(chain_key, ANCHOR_DOMAIN + jcs_dumps(without), hashlib.sha256).hexdigest()


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
    target = Path(path)
    body: dict[str, Any] = {
        "version": ANCHOR_VERSION,
        "chain_epoch": chain_epoch,
        "chain_seq": chain_seq,
        "event_id": event_id,
        "event_mac": event_mac,
        "updated_at": now,
    }
    body["anchor_mac"] = _anchor_mac(chain_key, body)

    temp = target.with_name(f"{target.name}.tmp.{secrets.token_hex(8)}")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(body).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def read_anchor(path: str | Path, chain_key: bytes) -> dict[str, Any]:
    """Read and authenticate. Every check below fails closed, never warns."""
    target = Path(path)
    try:
        info = os.lstat(target)
    except OSError as exc:
        raise AnchorError("anchor is unreadable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AnchorError("anchor must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise AnchorError("anchor must be a regular file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise AnchorError("anchor permissions must be 0600")
    if info.st_uid != os.geteuid():
        raise AnchorError("anchor must be owned by the runtime account")

    parent = os.stat(target.parent)
    if stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.geteuid():
        raise AnchorError("anchor directory must be 0700 and owned by the runtime account")

    try:
        body = json.loads(target.read_text())
    except ValueError as exc:
        raise AnchorError("anchor is not valid JSON") from exc
    if body.get("version") != ANCHOR_VERSION:
        raise AnchorError("unsupported anchor version")
    if not hmac.compare_digest(_anchor_mac(chain_key, body), str(body.get("anchor_mac", ""))):
        raise AnchorError("anchor MAC does not verify")
    return body


def latch_degraded(conn: sqlite3.Connection, *, reason: str, disclosure_ref: str = "") -> None:
    """The single degraded latch (design §6.8). Set only, never cleared here."""
    from comms.transports.telegram.storage.settings import set_setting

    set_setting(conn, "audit.integrity_degraded", 1)
    set_setting(conn, "audit.degraded_disclosure_ref", disclosure_ref)
    set_setting(conn, "audit.degraded_reason", reason)


def derive_integrity(conn: sqlite3.Connection, chain_key: bytes, path: str | Path) -> str:
    """Recompute integrity from the anchor and the chain (design §6.7).

    Equality alone never proclaims integrity: the head MAC must verify, the
    sequence must be continuous, and the retained chain must verify.
    """
    try:
        anchor = read_anchor(path, chain_key)
    except AnchorError:
        return FAIL_CLOSED

    current = head(conn)
    if current is None:
        return FAIL_CLOSED if anchor["chain_seq"] > 0 else CLEAN

    if (anchor["chain_epoch"], anchor["chain_seq"]) > (
        current["chain_epoch"],
        current["chain_seq"],
    ):
        # The database lost committed history. Moving the pointer would be
        # laundering the evidence, so this is fatal, not repairable.
        return FAIL_CLOSED

    try:
        verify_chain(conn, chain_key)
    except ChainError:
        return FAIL_CLOSED

    if anchor["chain_epoch"] != current["chain_epoch"]:
        return FAIL_CLOSED

    gap = current["chain_seq"] - anchor["chain_seq"]
    if gap == 0:
        return CLEAN if anchor["event_mac"] == current["event_mac"] else FAIL_CLOSED
    if gap == 1:
        return RECOVERY_REQUIRED
    return FAIL_CLOSED


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
        immediate_transaction,
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

    with immediate_transaction(conn):  # 4 appends are legal again
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
