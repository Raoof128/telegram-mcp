"""Retired HMAC secrets are kept only while a proven dependency exists (comms v0.3 A10).

- ``principal-key``: a dependency while the legacy ``principals`` table holds a row, since
  verifying that row recomputes its principal digest.
- ``privacy-key``: a dependency while any legacy ``exposure_ledger`` row lies inside the
  rolling budget window (the retired budget could still be asked about it).

Core never reads the Telegram schema's settings or key files: the caller supplies the
window and a callback that destroys the material, and this module decides whether it may.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from comms.core import timeutil

__all__ = ["RETIRED_HMAC", "RetiredKeyError", "destroy_retired_hmac", "retired_hmac_status"]

RETIRED_HMAC = ("principal-key", "privacy-key")
Status = Literal["dependency_proven", "destroyable"]


class RetiredKeyError(Exception):
    """A retired key operation was refused. Fixed messages."""


def retired_hmac_status(
    legacy_conn: Any, *, window_minutes: int, now: datetime
) -> dict[str, Status]:
    principals = legacy_conn.execute("SELECT count(*) FROM principals").fetchone()[0]
    since = timeutil.utc(now) - timedelta(minutes=int(window_minutes))
    recent = any(
        timeutil.instant(row[0]) >= since
        for row in legacy_conn.execute("SELECT ts FROM exposure_ledger")
    )
    return {
        "principal-key": "dependency_proven" if principals else "destroyable",
        "privacy-key": "dependency_proven" if recent else "destroyable",
    }


def destroy_retired_hmac(
    legacy_conn: Any,
    purpose: str,
    destroy: Callable[[], None],
    *,
    window_minutes: int,
    now: datetime,
) -> None:
    """Run ``destroy`` only if ``purpose`` is a retired HMAC key with no proven dependency."""
    if purpose not in RETIRED_HMAC:
        raise RetiredKeyError("not a retired HMAC key")
    status = retired_hmac_status(legacy_conn, window_minutes=window_minutes, now=now)[purpose]
    if status != "destroyable":
        raise RetiredKeyError("a proven dependency still needs this key")
    destroy()
