"""The external audit-head anchor engine: the single copy (comms v0.3 §A.5, A8).

A 0600 file in a 0700 directory owned by the runtime account, holding the head's
coordinates and an HMAC under the chain key with the profile's domain. A refresh is
durable or it did not happen: temp file, fsync, rename, fsync the directory. It never
runs inside a comms.db transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from comms.core import domains
from comms.core.audit.chain import ChainError, ChainProfile, head, verify_chain
from comms.core.canonical import jcs_dumps
from comms.core.storage.db import io_guard

__all__ = [
    "CLEAN",
    "COMMS_ANCHOR",
    "FAIL_CLOSED",
    "RECOVERY_REQUIRED",
    "AnchorError",
    "AnchorProfile",
    "anchor_mac",
    "derive_integrity",
    "read_anchor",
    "write_anchor",
]

ANCHOR_VERSION = 1
CLEAN = "CLEAN"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
FAIL_CLOSED = "FAIL_CLOSED"


class AnchorError(Exception):
    """The anchor is unreadable, unauthenticated or out of contract."""


@dataclass(frozen=True)
class AnchorProfile:
    name: str
    domain: bytes


COMMS_ANCHOR = AnchorProfile("comms", domains.AUDIT_HEAD_ANCHOR)


def anchor_mac(profile: AnchorProfile, chain_key: bytes, body: dict[str, Any]) -> str:
    without = {k: v for k, v in body.items() if k != "anchor_mac"}
    return hmac.new(chain_key, profile.domain + jcs_dumps(without), hashlib.sha256).hexdigest()


def write_anchor(
    profile: AnchorProfile,
    path: str | Path,
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    event_id: str,
    event_mac: str,
    now: str,
    guard_conn: Any = None,
) -> None:
    """Durable refresh: temp write, fsync, rename, fsync the directory."""
    if guard_conn is not None:
        io_guard(guard_conn)
    target = Path(path)
    body: dict[str, Any] = {
        "version": ANCHOR_VERSION,
        "chain_epoch": chain_epoch,
        "chain_seq": chain_seq,
        "event_id": event_id,
        "event_mac": event_mac,
        "updated_at": now,
    }
    body["anchor_mac"] = anchor_mac(profile, chain_key, body)
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


def read_anchor(profile: AnchorProfile, path: str | Path, chain_key: bytes) -> dict[str, Any]:
    """Read and authenticate. Every check fails closed."""
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
    if not isinstance(body, dict) or body.get("version") != ANCHOR_VERSION:
        raise AnchorError("unsupported anchor version")
    if not hmac.compare_digest(
        anchor_mac(profile, chain_key, body), str(body.get("anchor_mac", ""))
    ):
        raise AnchorError("anchor MAC does not verify")
    return body


def derive_integrity(
    conn: Any,
    chain_profile: ChainProfile,
    anchor_profile: AnchorProfile,
    key_for_epoch: Callable[[int], bytes],
    path: str | Path,
    *,
    public_for: Callable[[str], bytes | None] | None = None,
) -> str:
    """CLEAN, RECOVERY_REQUIRED (anchor exactly one behind a verified head) or FAIL_CLOSED."""
    current = head(conn, chain_profile)
    epoch = current["chain_epoch"] if current else 1
    try:
        anchor = read_anchor(anchor_profile, path, key_for_epoch(epoch))
    except AnchorError:
        return FAIL_CLOSED
    if current is None:
        return FAIL_CLOSED if anchor["chain_seq"] > 0 else CLEAN
    if (anchor["chain_epoch"], anchor["chain_seq"]) > (
        current["chain_epoch"],
        current["chain_seq"],
    ):
        return FAIL_CLOSED  # the database lost committed history
    try:
        verify_chain(conn, chain_profile, key_for_epoch, public_for=public_for)
    except ChainError:
        return FAIL_CLOSED
    if anchor["chain_epoch"] != current["chain_epoch"]:
        return FAIL_CLOSED
    gap = current["chain_seq"] - anchor["chain_seq"]
    if gap == 0:
        return CLEAN if anchor["event_mac"] == current["event_mac"] else FAIL_CLOSED
    return RECOVERY_REQUIRED if gap == 1 else FAIL_CLOSED
