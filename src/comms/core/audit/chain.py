"""The profile-parameterised audit chain engine: the single copy (comms v0.3 §A.5, R-002).

An event's MAC is HMAC(key, event_domain ‖ u64(epoch) ‖ u64(seq) ‖ prev_mac ‖ JCS(event
columns)); the first event of an epoch links to ``genesis_mac(profile, epoch)``. Checkpoints
are Ed25519 signatures over checkpoint_domain ‖ JCS(signed fields). A profile fixes the
domains, the event columns and the minters; the legacy Telegram profile lives in the
Telegram package, the comms profile here. The caller owns every transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from comms.core import domains, refs
from comms.core.canonical import jcs_dumps
from comms.core.keys import ids

__all__ = [
    "COMMS",
    "ChainError",
    "ChainProfile",
    "append_event",
    "append_guard",
    "checkpoint_message",
    "event_mac",
    "genesis_mac",
    "head",
    "insert_checkpoint",
    "verify_chain",
    "verify_checkpoints",
]

CHAIN_COLUMNS = ("chain_epoch", "chain_seq", "prev_event_mac", "event_mac")
_BASE_SIGNED = ("chain_epoch", "chain_seq", "last_event_id", "last_event_mac", "created_at")


class ChainError(Exception):
    """The chain is inconsistent, or an event is out of its profile's contract."""


@dataclass(frozen=True)
class ChainProfile:
    name: str
    event_domain: bytes
    genesis_domain: bytes
    checkpoint_domain: bytes
    event_columns: tuple[str, ...]
    validate_event: Callable[[Mapping[str, Any]], None] = field(repr=False)
    mint_checkpoint_ref: Callable[[], str] = field(repr=False)
    checkpoint_has_reason: bool = False
    events_table: str = "audit_events"
    checkpoints_table: str = "audit_checkpoints"

    @property
    def signed_checkpoint_fields(self) -> tuple[str, ...]:
        return (*_BASE_SIGNED, "reason") if self.checkpoint_has_reason else _BASE_SIGNED


def _validate_comms_event(event: Mapping[str, Any]) -> None:
    try:
        refs.check(event["event_id"], "audit_event")
    except (KeyError, ValueError):
        raise ChainError("comms audit event id is invalid") from None
    if not isinstance(event.get("kind"), str) or not isinstance(event.get("payload"), str):
        raise ChainError("comms audit event is out of contract")


COMMS = ChainProfile(
    name="comms",
    event_domain=domains.AUDIT_CHAIN,
    genesis_domain=domains.AUDIT_GENESIS,
    checkpoint_domain=domains.AUDIT_CHECKPOINT,
    event_columns=("event_id", "ts", "kind", "subject_ref", "subject_digest", "payload"),
    validate_event=_validate_comms_event,
    mint_checkpoint_ref=lambda: refs.mint("audit_checkpoint"),
    checkpoint_has_reason=True,
)

_GUARDS: dict[str, threading.Lock] = {}
_GUARDS_LOCK = threading.Lock()


def append_guard(profile: ChainProfile) -> threading.Lock:
    """One process-wide lock per chain profile: every append to that chain takes it."""
    with _GUARDS_LOCK:
        return _GUARDS.setdefault(profile.name, threading.Lock())


def _require_tx(conn: Any) -> None:
    if not conn.in_transaction:
        raise ChainError("an open BEGIN IMMEDIATE transaction is required")


def genesis_mac(profile: ChainProfile, chain_epoch: int) -> str:
    return hashlib.sha256(profile.genesis_domain + chain_epoch.to_bytes(8, "big")).hexdigest()


def event_mac(
    profile: ChainProfile,
    chain_key: bytes,
    *,
    chain_epoch: int,
    chain_seq: int,
    prev_event_mac: str,
    event: Mapping[str, Any],
) -> str:
    message = (
        profile.event_domain
        + chain_epoch.to_bytes(8, "big")
        + chain_seq.to_bytes(8, "big")
        + bytes.fromhex(prev_event_mac)
        + jcs_dumps({k: event[k] for k in profile.event_columns})
    )
    return hmac.new(chain_key, message, hashlib.sha256).hexdigest()


def head(conn: Any, profile: ChainProfile) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT chain_epoch, chain_seq, event_id, event_mac FROM {profile.events_table}"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("chain_epoch", "chain_seq", "event_id", "event_mac"), row, strict=True))


def append_event(
    conn: Any, profile: ChainProfile, chain_key: bytes, event: Mapping[str, Any]
) -> dict[str, Any]:
    """Append one event inside the caller's open transaction."""
    _require_tx(conn)
    if any(c not in event for c in profile.event_columns):
        raise ChainError("event is missing required columns")
    profile.validate_event(event)
    current = head(conn, profile)
    if current is None:
        chain_epoch, chain_seq, prev = 1, 1, genesis_mac(profile, 1)
    else:
        chain_epoch, chain_seq, prev = (
            current["chain_epoch"],
            current["chain_seq"] + 1,
            current["event_mac"],
        )
    mac = event_mac(
        profile,
        chain_key,
        chain_epoch=chain_epoch,
        chain_seq=chain_seq,
        prev_event_mac=prev,
        event=event,
    )
    columns = ", ".join((*profile.event_columns, *CHAIN_COLUMNS))
    marks = ", ".join("?" * (len(profile.event_columns) + len(CHAIN_COLUMNS)))
    conn.execute(
        f"INSERT INTO {profile.events_table} ({columns}) VALUES ({marks})",
        (*(event[c] for c in profile.event_columns), chain_epoch, chain_seq, prev, mac),
    )
    return {
        "chain_epoch": chain_epoch,
        "chain_seq": chain_seq,
        "event_id": event["event_id"],
        "prev_event_mac": prev,
        "event_mac": mac,
    }


def verify_chain(
    conn: Any,
    profile: ChainProfile,
    key_for_epoch: Callable[[int], bytes],
    *,
    root: Mapping[str, Any] | None = None,
) -> None:
    """Recompute every retained link (epochs: Part B).

    Without ``root`` the chain must start at its genesis. With ``root`` (a checkpoint row
    whose signature the caller has verified) the first retained event must be exactly the
    root's last event: its MAC recomputes and equals the signed ``last_event_mac``, and its
    link backwards is vouched for by the signature instead of by deleted rows.
    """
    names = (*profile.event_columns, *CHAIN_COLUMNS)
    rows = conn.execute(
        f"SELECT {', '.join(names)} FROM {profile.events_table} ORDER BY chain_epoch, chain_seq"
    ).fetchall()
    if root is not None and not rows:
        raise ChainError("the root's event row is missing")
    expected_prev: str | None = None
    expected_seq: int | None = None
    for row in rows:
        record = dict(zip(names, row, strict=True))
        if expected_seq is None and root is not None:
            if (record["chain_epoch"], record["chain_seq"], record["event_id"]) != (
                root["chain_epoch"],
                root["chain_seq"],
                root["last_event_id"],
            ) or not hmac.compare_digest(record["event_mac"], root["last_event_mac"]):
                raise ChainError("the first retained event is not the root's event")
            expected_prev, expected_seq = record["prev_event_mac"], record["chain_seq"]
        if expected_seq is None:
            expected_prev, expected_seq = genesis_mac(profile, record["chain_epoch"]), 1
        if record["chain_seq"] != expected_seq:
            raise ChainError("chain sequence is not continuous")
        if record["prev_event_mac"] != expected_prev:
            raise ChainError("chain link does not match the previous event")
        recomputed = event_mac(
            profile,
            key_for_epoch(record["chain_epoch"]),
            chain_epoch=record["chain_epoch"],
            chain_seq=record["chain_seq"],
            prev_event_mac=record["prev_event_mac"],
            event=record,
        )
        if not hmac.compare_digest(recomputed, record["event_mac"]):
            raise ChainError("event MAC does not verify")
        expected_prev, expected_seq = record["event_mac"], record["chain_seq"] + 1


def checkpoint_message(profile: ChainProfile, row: Mapping[str, Any]) -> bytes:
    return profile.checkpoint_domain + jcs_dumps(
        {k: row[k] for k in profile.signed_checkpoint_fields}
    )


def insert_checkpoint(
    conn: Any, profile: ChainProfile, checkpoint_key: bytes, *, now: str, reason: str | None = None
) -> dict[str, Any]:
    """Sign the current head inside the caller's transaction. Never commits."""
    _require_tx(conn)
    if profile.checkpoint_has_reason != (reason is not None):
        raise ChainError("checkpoint reason does not match the profile")
    current = head(conn, profile)
    if current is None:
        raise ChainError("cannot checkpoint an empty chain")
    row: dict[str, Any] = {
        "checkpoint_ref": profile.mint_checkpoint_ref(),
        "chain_epoch": current["chain_epoch"],
        "chain_seq": current["chain_seq"],
        "last_event_id": current["event_id"],
        "last_event_mac": current["event_mac"],
        "created_at": now,
    }
    if reason is not None:
        row["reason"] = reason
    private = Ed25519PrivateKey.from_private_bytes(checkpoint_key)
    signature = private.sign(checkpoint_message(profile, row))
    signing_key_id = ids.ed25519_key_id(private.public_key().public_bytes_raw())
    columns = (*row, "signing_key_id", "signature")
    conn.execute(
        f"INSERT INTO {profile.checkpoints_table} ({', '.join(columns)})"
        f" VALUES ({', '.join('?' * len(columns))})",
        (*row.values(), signing_key_id, signature.hex()),
    )
    return {**row, "signing_key_id": signing_key_id, "signature": signature.hex()}


def verify_checkpoints(
    conn: Any, profile: ChainProfile, public_for: Callable[[str], bytes | None]
) -> None:
    """Verify every retained checkpoint by its own recorded key; the ID is recomputed."""
    names = (*profile.signed_checkpoint_fields, "signing_key_id", "signature")
    rows = conn.execute(
        f"SELECT {', '.join(names)} FROM {profile.checkpoints_table}"
        " ORDER BY chain_epoch, chain_seq"
    ).fetchall()
    for row in rows:
        record = dict(zip(names, row, strict=True))
        public = public_for(record["signing_key_id"])
        if public is None or ids.ed25519_key_id(public) != record["signing_key_id"]:
            raise ChainError("checkpoint signing key is unknown")
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(
                bytes.fromhex(record["signature"]), checkpoint_message(profile, record)
            )
        except (InvalidSignature, ValueError):
            raise ChainError("checkpoint signature does not verify") from None
