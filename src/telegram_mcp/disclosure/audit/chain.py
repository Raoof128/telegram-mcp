"""MAC-linked audit chain (frozen spec §26.5; design §6.1, §6.2).

Strictly linear, one event per sequence number, appended under
``BEGIN IMMEDIATE`` so concurrent completions cannot fork the chain.

``event_id`` is inside the MAC input, so its format is part of the chain
and is frozen here rather than left to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "ADMIN_EVENTS",
    "EVENT_DOMAIN",
    "GENESIS_DOMAIN",
    "ChainError",
    "append_event",
    "event_mac",
    "genesis_mac",
    "head",
    "immediate_transaction",
    "mint_event_id",
    "require_immediate_transaction",
    "verify_chain",
]

EVENT_DOMAIN = b"telegram-mcp-audit-v1"
GENESIS_DOMAIN = b"telegram-mcp-audit-genesis-v1"

# Non-tool chain events. audit_events.tool_name is NOT NULL and §6.5 sends
# administrative and security events through the same barrier, so they need
# values. The dotted prefix cannot collide with the ten tool names.
ADMIN_EVENTS: tuple[str, ...] = (
    "admin.lock",
    "admin.unlock",
    "admin.key_rotation",
    "admin.repair_anchor",
    "admin.policy_import",
)

_TOOLS = (
    "telegram_status",
    "telegram_list_projects",
    "telegram_resolve_project",
    "telegram_list_chats",
    "telegram_resolve_peer",
    "telegram_get_unread",
    "telegram_get_messages",
    "telegram_get_context",
    "telegram_search_messages",
    "telegram_cross_project_search",
)
_ALLOWED_TOOL_NAMES = frozenset(_TOOLS) | frozenset(ADMIN_EVENTS)

# Appendix C illustrates evt_01J...: Crockford base32, uppercase, 26 chars.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_EVENT_COLUMNS = (
    "event_id",
    "ts",
    "tool_name",
    "principal_ref",
    "client_ref",
    "account_ref",
    "peer_ref",
    "project_ref",
    "project_count",
    "policy_epoch",
    "result_count",
    "duration_ms",
    "telegram_rpc_count",
    "status",
    "error_code",
    "disclosure_ref",
)


class ChainError(Exception):
    """The chain is inconsistent, or an event is out of contract."""


def require_immediate_transaction(conn: sqlite3.Connection) -> None:
    """Refuse to append outside a caller-owned write transaction."""
    if not conn.in_transaction:
        raise ChainError("append_event requires an open BEGIN IMMEDIATE transaction")


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """The transaction the coordinator's step 11 runs inside."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _base32_crockford(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_CROCKFORD[remainder])
    return "".join(reversed(chars))


def mint_event_id() -> str:
    """``evt_`` plus a 26-character Crockford base32 ULID."""
    timestamp = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return "evt_" + _base32_crockford(timestamp, 10) + _base32_crockford(randomness, 16)


def genesis_mac(chain_epoch: int) -> str:
    """The explicit genesis value for ``chain_seq = 1``, bound to the epoch."""
    payload = GENESIS_DOMAIN + chain_epoch.to_bytes(8, "big")
    return hashlib.sha256(payload).hexdigest()


def event_mac(
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    prev_event_mac: str,
    event: Mapping[str, Any],
) -> str:
    """HMAC over the domain, coordinates, previous link and canonical event."""
    message = (
        EVENT_DOMAIN
        + chain_epoch.to_bytes(8, "big")
        + chain_seq.to_bytes(8, "big")
        + bytes.fromhex(prev_event_mac)
        + jcs_dumps({k: event[k] for k in _EVENT_COLUMNS})
    )
    return hmac.new(chain_key, message, hashlib.sha256).hexdigest()


def head(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The last appended event, or ``None`` for an empty chain."""
    row = conn.execute(
        "SELECT chain_epoch, chain_seq, event_id, event_mac FROM audit_events"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("chain_epoch", "chain_seq", "event_id", "event_mac"), row, strict=True))


def append_event(
    conn: sqlite3.Connection, chain_key: bytes, event: Mapping[str, Any]
) -> dict[str, Any]:
    """Append one event. **The caller owns the transaction.**

    This function does NOT open or commit a transaction, and that is
    load-bearing rather than a style choice. §23A.3 requires the disclosure
    commit to be **one** transaction containing the exposure-ledger rows,
    the receipt row and exactly one audit append. If this function opened its
    own ``BEGIN IMMEDIATE`` the coordinator could not wrap it — SQLite raises
    ``cannot start a transaction within a transaction`` — and the only way
    past that error would be to drop the coordinator's transaction, silently
    turning one atomic commit into three and destroying the crash model the
    whole design rests on.

    Callers must already hold an ``BEGIN IMMEDIATE`` transaction, so the head
    read and the insert are serialised against other writers and the chain
    cannot fork. ``require_immediate_transaction`` enforces that rather than
    trusting it.
    """
    require_immediate_transaction(conn)
    if event.get("tool_name") not in _ALLOWED_TOOL_NAMES:
        raise ChainError("event tool_name is not in the closed vocabulary")
    missing = [c for c in _EVENT_COLUMNS if c not in event]
    if missing:
        raise ChainError("event is missing required columns")

    current = head(conn)
    if current is None:
        chain_epoch, chain_seq = 1, 1
        prev = genesis_mac(chain_epoch)
    else:
        chain_epoch = current["chain_epoch"]
        chain_seq = current["chain_seq"] + 1
        prev = current["event_mac"]

    mac = event_mac(
        chain_key,
        chain_epoch=chain_epoch,
        chain_seq=chain_seq,
        prev_event_mac=prev,
        event=event,
    )
    columns = ", ".join(
        (*_EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac")
    )
    marks = ", ".join("?" * (len(_EVENT_COLUMNS) + 4))
    conn.execute(
        f"INSERT INTO audit_events ({columns}) VALUES ({marks})",
        (*(event[c] for c in _EVENT_COLUMNS), chain_epoch, chain_seq, prev, mac),
    )

    return {
        "chain_epoch": chain_epoch,
        "chain_seq": chain_seq,
        "event_id": event["event_id"],
        "prev_event_mac": prev,
        "event_mac": mac,
    }


def verify_chain(conn: sqlite3.Connection, chain_key: bytes) -> None:
    """Recompute every retained link. Raises ``ChainError`` on any mismatch."""
    columns = ", ".join(
        (*_EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac")
    )
    rows = conn.execute(
        f"SELECT {columns} FROM audit_events ORDER BY chain_epoch, chain_seq"
    ).fetchall()

    expected_prev: str | None = None
    expected_seq: int | None = None
    for row in rows:
        record = dict(
            zip(
                (*_EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac"),
                row,
                strict=True,
            )
        )
        if expected_seq is None:
            expected_prev = genesis_mac(record["chain_epoch"])
            expected_seq = 1
        if record["chain_seq"] != expected_seq:
            raise ChainError("chain sequence is not continuous")
        if record["prev_event_mac"] != expected_prev:
            raise ChainError("chain link does not match the previous event")
        recomputed = event_mac(
            chain_key,
            chain_epoch=record["chain_epoch"],
            chain_seq=record["chain_seq"],
            prev_event_mac=record["prev_event_mac"],
            event=record,
        )
        if not hmac.compare_digest(recomputed, record["event_mac"]):
            raise ChainError("event MAC does not verify")
        expected_prev = record["event_mac"]
        expected_seq = record["chain_seq"] + 1
