"""The mirrored customer-service window (comms v0.3 Task C23; A22).

Webhook ingestion records when the customer last wrote, with its observation time and source
event, into state Comms owns. The freeze reads it inside its own transaction and copies it into
the ``DeliveryIntent``; ``prepare`` never reads an archive. The mirror only moves forward.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.storage.db import write_tx

__all__ = ["mirror_window", "mirror_window_in_tx", "window_fact"]


def mirror_window(
    conn: Any,
    identity_id: int,
    last_customer_message_at: str,
    source_event_ref: str,
    *,
    now: datetime,
) -> bool:
    """Record a customer message time; returns whether the mirror moved forward."""
    with write_tx(conn):
        return mirror_window_in_tx(
            conn, identity_id, last_customer_message_at, source_event_ref, now=now
        )


def mirror_window_in_tx(
    conn: Any,
    identity_id: int,
    last_customer_message_at: str,
    source_event_ref: str,
    *,
    now: datetime,
) -> bool:
    """``mirror_window`` inside the caller's transaction (the webhook worker, C29)."""
    if not conn.in_transaction:
        raise RuntimeError("mirror_window_in_tx needs an open transaction")
    at = timeutil.instant(last_customer_message_at)  # refuses anything but a stored time
    if not isinstance(source_event_ref, str) or not source_event_ref:
        raise ValueError("a window fact names its source event")
    current = window_fact(conn, identity_id)
    if current is not None and timeutil.instant(current["last_customer_message_at"]) >= at:
        return False
    conn.execute(
        "INSERT INTO endpoint_window (identity_id, last_customer_message_at, observed_at, source_event_ref)"
        " VALUES (?, ?, ?, ?) ON CONFLICT (identity_id) DO UPDATE SET"
        " last_customer_message_at = excluded.last_customer_message_at,"
        " observed_at = excluded.observed_at, source_event_ref = excluded.source_event_ref",
        (identity_id, last_customer_message_at, timeutil.iso(now), source_event_ref),
    )
    return True


def window_fact(conn: Any, identity_id: int) -> dict[str, str] | None:
    row = conn.execute(
        "SELECT last_customer_message_at, observed_at, source_event_ref FROM endpoint_window"
        " WHERE identity_id = ?",
        (identity_id,),
    ).fetchone()
    if row is None:
        return None
    return {"last_customer_message_at": row[0], "observed_at": row[1], "source_event_ref": row[2]}
