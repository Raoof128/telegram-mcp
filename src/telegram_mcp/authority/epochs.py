"""Epoch operations over row-shaped state (spec §10.6, §10.8, §12.2, §9.3).

Three epochs exist. ``policy_epoch`` is per ``(principal, account)`` and
advances on any owner-scope mutation (spec §10.6). ``project_epoch`` is per
project and advances on any membership change (spec §10.8). The global
``security_epoch`` lives in the ``security_state`` singleton, starts at 1
with ``locked=0`` (spec §12.2), and advances on every lock *and* every
unlock, so no authority object minted before a lock can ever be revived
(spec §9.3 / design §5).

State is a plain dict shaped like the Task-7 rows so these functions stay
storage-free; ``storage.bind_epoch_state`` loads the rows into this shape
and writes them back transactionally. Callers that advance the security
epoch must also sweep the in-memory consent broker and cursor store: the
epoch change makes those objects fail closed on their next use, and the
sweep releases them immediately (design §5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

__all__ = [
    "DEFAULT_POLICY_KEY",
    "PresenceRequired",
    "bump_policy_epoch",
    "bump_project_epoch",
    "new_epoch_state",
    "set_locked",
]

# V0.1.10 is single-owner (spec §10.5): one policy_state row, this key.
DEFAULT_POLICY_KEY = ("local_single_principal", "local_account")

EpochState = dict[str, Any]


class PresenceRequired(Exception):
    """Unlock demands the trusted user-presence path (spec §9.3)."""


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_epoch_state(
    *,
    security_epoch: int = 1,
    locked: bool = False,
    policy_epoch: int = 1,
    policy_key: tuple[str, str] = DEFAULT_POLICY_KEY,
    projects: dict[str, int] | None = None,
    now: str | None = None,
) -> EpochState:
    """Build epoch state; defaults match the spec's initial singleton row."""
    stamp = now or _now_iso()
    return {
        "security_state": {
            "singleton_id": 1,
            "security_epoch": security_epoch,
            "locked": 1 if locked else 0,
            "locked_at": stamp if locked else None,
            "updated_at": stamp,
        },
        "policy_state": {policy_key: {"policy_epoch": policy_epoch, "updated_at": stamp}},
        "projects": {
            ref: {"project_epoch": epoch, "updated_at": stamp}
            for ref, epoch in (projects or {}).items()
        },
    }


def bump_policy_epoch(
    state: EpochState, key: tuple[str, str] | None = None, *, now: str | None = None
) -> int:
    """Advance one ``policy_state`` row; return the new epoch."""
    rows = state["policy_state"]
    if key is None:
        if len(rows) != 1:
            raise ValueError("ambiguous policy_state row: pass an explicit key")
        key = next(iter(rows))
    row = rows[key]
    row["policy_epoch"] = int(row["policy_epoch"]) + 1
    row["updated_at"] = now or _now_iso()
    return int(row["policy_epoch"])


def bump_project_epoch(state: EpochState, project: str, *, now: str | None = None) -> int:
    """Advance one project's epoch; return the new epoch.

    A missing project raises ``KeyError``: epochs are never created as a
    side effect of a membership change.
    """
    row = state["projects"][project]
    row["project_epoch"] = int(row["project_epoch"]) + 1
    row["updated_at"] = now or _now_iso()
    return int(row["project_epoch"])


def set_locked(
    state: EpochState, locked: bool, *, presence: bool = False, now: str | None = None
) -> int:
    """Lock or unlock; always advances the security epoch.

    Unlock requires ``presence=True`` (the trusted user-presence path) and
    raises :class:`PresenceRequired` otherwise, leaving the state untouched
    so a failed unlock attempt cannot burn an epoch.
    """
    if not locked and not presence:
        raise PresenceRequired("unlock requires the user-presence path")
    stamp = now or _now_iso()
    row = state["security_state"]
    row["security_epoch"] = int(row["security_epoch"]) + 1
    row["locked"] = 1 if locked else 0
    row["locked_at"] = stamp if locked else None
    row["updated_at"] = stamp
    return int(row["security_epoch"])
