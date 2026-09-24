"""`audit verify --all`: legacy chain → legacy seal → lineage → comms genesis → comms chain → anchor.

Comms v0.3 A6 and G6. Every check runs and reports a fixed problem code; a check that
cannot be performed because an earlier link is missing reports that it was skipped, never
that it passed. ``ok`` is true only when there are no problems. The legacy chain may have
been truncated behind a signed root (B17): verification then starts at that root.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from comms.core.audit import cutover
from comms.core.audit.anchor import COMMS_ANCHOR, AnchorError, read_anchor
from comms.core.audit.chain import (
    COMMS,
    ChainError,
    ChainProfile,
    head,
    verify_chain,
    verify_checkpoints,
)
from comms.core.keys import ids

__all__ = ["LegacyVerify", "VerifyKeys", "VerifyReport", "verify_all"]

_GENESIS_KIND = "system.audit_cutover"


@dataclass(frozen=True)
class LegacyVerify:
    """What the legacy transport supplies; core never reads its schema beyond the chain tables."""

    profile: ChainProfile
    chain_domain: str
    key_for_epoch: Callable[[int], bytes]
    public_for: Callable[[str], bytes | None]
    is_final_marker: Callable[[Mapping[str, Any]], bool]
    is_sealed: Callable[[Any], bool]


@dataclass(frozen=True)
class VerifyKeys:
    legacy: LegacyVerify
    comms_key_for_epoch: Callable[[int], bytes]
    comms_anchor_path: Path
    comms_public_for: Callable[[str], bytes | None]  # epoch seals are signed (design §B.1)


@dataclass(frozen=True)
class VerifyReport:
    legacy: str  # "ok" | "fail"
    lineage: str  # "ok" | "fail" | "skipped"
    comms: str  # "ok" | "fail"
    ok: bool
    problems: tuple[str, ...]


def _rows(
    conn: Any, sql: str, names: tuple[str, ...], args: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return [dict(zip(names, row, strict=True)) for row in conn.execute(sql, args).fetchall()]


def _legacy_root(conn: Any, profile: ChainProfile) -> Mapping[str, Any] | None:
    """The checkpoint signing the first retained event, when the chain no longer starts at 1."""
    first = conn.execute(
        f"SELECT chain_epoch, chain_seq FROM {profile.events_table}"
        " ORDER BY chain_epoch, chain_seq LIMIT 1"
    ).fetchone()
    if first is None or tuple(first) == (1, 1):
        return None
    names = ("chain_epoch", "chain_seq", "last_event_id", "last_event_mac")
    roots = _rows(
        conn,
        f"SELECT {', '.join(names)} FROM {profile.checkpoints_table}"
        " WHERE chain_epoch = ? AND chain_seq = ?",
        names,
        (first[0], first[1]),
    )
    if len(roots) != 1:
        raise ChainError("a truncated chain has no signed root")
    return roots[0]


def _legacy(conn: Any, lv: LegacyVerify, problems: list[str]) -> cutover.LegacySeal | None:
    profile = lv.profile
    try:
        verify_checkpoints(conn, profile, lv.public_for)
        verify_chain(conn, profile, lv.key_for_epoch, root=_legacy_root(conn, profile))
    except ChainError:
        problems.append("LEGACY_CHAIN_INVALID")
    finals = _rows(
        conn,
        f"SELECT {', '.join(cutover.SEAL_CHECKPOINT_FIELDS)} FROM {profile.checkpoints_table}"
        " ORDER BY chain_epoch DESC, chain_seq DESC LIMIT 1",
        cutover.SEAL_CHECKPOINT_FIELDS,
    )
    current = head(conn, profile)
    if not finals or current is None or not lv.is_sealed(conn):
        problems.append("LEGACY_SEAL_MISSING")
        return None
    final = finals[0]
    events = _rows(
        conn,
        f"SELECT {', '.join(profile.event_columns)} FROM {profile.events_table}"
        " WHERE chain_epoch = ? AND chain_seq = ?",
        profile.event_columns,
        (final["chain_epoch"], final["chain_seq"]),
    )
    if (
        len(events) != 1
        or current["event_id"] != final["last_event_id"]
        or events[0]["event_id"] != final["last_event_id"]
        or not lv.is_final_marker(events[0])
    ):
        problems.append("LEGACY_SEAL_MISSING")
        return None
    return cutover.seal_of(final)


def _lineage(
    conn: Any, seal: cutover.LegacySeal | None, keys: VerifyKeys, problems: list[str]
) -> str:
    names = (*cutover.LINEAGE_COLUMNS, "lineage_digest")
    rows = _rows(conn, f"SELECT {', '.join(names)} FROM audit_lineage", names)
    geneses = conn.execute(
        "SELECT count(*) FROM audit_events WHERE kind = ?", (_GENESIS_KIND,)
    ).fetchone()[0]
    if not rows:
        problems.append("LINEAGE_MISSING")
        return "fail"
    if len(rows) > 1 or geneses > 1:
        problems.append("LINEAGE_DUPLICATE")
        return "fail"
    if seal is None:
        problems.append("LINEAGE_UNVERIFIED")
        return "skipped"
    row = rows[0]
    stored = row.pop("lineage_digest")
    expected = cutover.expected_lineage(row["cutover_ref"], seal, keys.legacy.chain_domain)
    if any(row[k] != v for k, v in expected.items()) or not hmac.compare_digest(
        cutover.lineage_digest(row), stored
    ):
        problems.append("LINEAGE_SEAL_MISMATCH")
        return "fail"
    firsts = conn.execute(
        "SELECT chain_epoch, chain_seq, kind, subject_ref, subject_digest, payload"
        " FROM audit_events ORDER BY chain_epoch, chain_seq LIMIT 1"
    ).fetchall()
    key_id = ids.hmac_key_id(keys.comms_key_for_epoch(cutover.COMMS_FIRST_EPOCH))
    try:
        payload = json.loads(firsts[0][5]) if firsts else None
    except ValueError:
        payload = None
    if (
        not firsts
        or tuple(firsts[0][:5])
        != (
            cutover.COMMS_FIRST_EPOCH,
            1,
            _GENESIS_KIND,
            row["cutover_ref"],
            stored,
        )
        or payload != cutover.genesis_payload(row, key_id)
    ):
        problems.append("GENESIS_MISMATCH")
        return "fail"
    return "ok"


def _comms(conn: Any, keys: VerifyKeys, problems: list[str]) -> str:
    before = len(problems)
    try:
        verify_chain(conn, COMMS, keys.comms_key_for_epoch, public_for=keys.comms_public_for)
    except ChainError:
        problems.append("COMMS_CHAIN_INVALID")
    current = head(conn, COMMS)
    if current is None:
        problems.append("COMMS_CHAIN_EMPTY")
        return "fail"
    try:
        anchored = read_anchor(
            COMMS_ANCHOR, keys.comms_anchor_path, keys.comms_key_for_epoch(current["chain_epoch"])
        )
    except AnchorError:
        anchored = None
    fields = ("chain_epoch", "chain_seq", "event_id", "event_mac")
    if anchored is None or any(anchored[f] != current[f] for f in fields):
        problems.append("COMMS_ANCHOR_MISMATCH")
    return "ok" if len(problems) == before else "fail"


def verify_all(comms_conn: Any, legacy_conn: Any, keys: VerifyKeys) -> VerifyReport:
    problems: list[str] = []
    seal = _legacy(legacy_conn, keys.legacy, problems)
    legacy = "fail" if problems else "ok"
    lineage = _lineage(comms_conn, seal, keys, problems)
    comms = _comms(comms_conn, keys, problems)
    return VerifyReport(
        legacy=legacy, lineage=lineage, comms=comms, ok=not problems, problems=tuple(problems)
    )
