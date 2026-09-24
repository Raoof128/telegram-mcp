"""The truncation root (comms v0.3 A15, design §B.1): the latest verified checkpoint at or
before the cutoff — never simply the newest.

A candidate qualifies only if its signature verifies under its own recorded key and the
event it names is still stored with exactly the signed MAC. A candidate whose signature
fails is skipped and reported; one whose event is gone or differs is skipped.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from comms.core import timeutil
from comms.core.audit.chain import ChainError, ChainProfile, _checkpoint_rows, _verify_signature

__all__ = ["choose_root"]

# The comms stored form (timeutil) and the legacy Telegram checkpoint form, exactly.
_LEGACY = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


def _instant(text: object) -> datetime:
    if isinstance(text, str) and _LEGACY.fullmatch(text):
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return timeutil.parse(text)


def choose_root(
    conn: Any,
    profile: ChainProfile,
    *,
    cutoff: str,
    public_keys: Callable[[str], bytes | None],
    skipped: list[str] | None = None,
) -> dict[str, Any] | None:
    """The latest eligible checkpoint at or before ``cutoff``, or ``None``.

    ``skipped`` (if given) receives the ``checkpoint_ref`` of each candidate at or before
    the cutoff whose signature did not verify, newest first.
    """
    limit = _instant(cutoff)
    candidates = [c for c in _checkpoint_rows(conn, profile) if _instant(c["created_at"]) <= limit]
    for checkpoint in sorted(
        candidates,
        key=lambda c: (c["chain_epoch"], c["chain_seq"], _instant(c["created_at"])),
        reverse=True,
    ):
        try:
            _verify_signature(profile, checkpoint, public_keys)
        except ChainError:
            if skipped is not None:
                skipped.append(checkpoint["checkpoint_ref"])
            continue
        row = conn.execute(
            f"SELECT event_id, event_mac FROM {profile.events_table} WHERE chain_epoch = ? AND chain_seq = ?",
            (checkpoint["chain_epoch"], checkpoint["chain_seq"]),
        ).fetchone()
        if (
            row is None
            or row[0] != checkpoint["last_event_id"]
            or not hmac.compare_digest(row[1], checkpoint["last_event_mac"])
        ):
            continue
        return checkpoint
    return None
