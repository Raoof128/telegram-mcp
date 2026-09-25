"""``exposure status`` (spec §23C): retired in comms v0.3 with disclosure-on-read.

The exposure budget measured disclosures the retired read path made. Only the historical
harness (``runtime/legacy_composition``) routes this handler; production answers
``RETIRED_IN_V0_3`` before any handler runs.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from comms.transports.telegram.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    committed_usage,
    subject_digest,
    thresholds_for,
    window_start,
)
from comms.transports.telegram.ipc.handlers._wrapper import Handler
from comms.transports.telegram.ipc.handlers.inspect import _body
from comms.transports.telegram.storage.settings import get_setting

__all__ = ["exposure_handlers"]


def exposure_handlers(
    conn: sqlite3.Connection, *, clock: Callable[[], float] = time.time
) -> dict[str, Handler]:
    def exposure(args: dict[str, Any]) -> dict[str, Any]:
        body = _body(args, {"client_ref", "project_ref"})
        wanted = body.get("client_ref")
        clients = conn.execute(
            "SELECT id, client_ref FROM mcp_clients ORDER BY client_ref"
        ).fetchall()
        if wanted is not None:
            clients = [c for c in clients if c[1] == wanted]
            if not clients:
                raise ValueError("unknown client_ref")
        project = body.get("project_ref")
        if (
            project is not None
            and conn.execute("SELECT 1 FROM projects WHERE project_ref = ?", (project,)).fetchone()
            is None
        ):
            raise ValueError("unknown project_ref")
        minutes = get_setting(conn, "exposure_budget.rolling_window_minutes")
        since = window_start(clock(), minutes)

        def bucket(client_id: int, kind: str, subject: str = "") -> dict[str, Any]:
            used = committed_usage(
                conn, BucketKey(client_id, kind, subject_digest(kind, subject)), since=since
            )
            limits = thresholds_for(conn, kind)
            return {
                "records": used.records,
                "bytes": used.bytes,
                "soft_records": limits.soft_records,
                "hard_records": limits.hard_records,
                "soft_bytes": limits.soft_bytes,
                "hard_bytes": limits.hard_bytes,
            }

        rows = []
        for client_id, client_ref in clients:
            row: dict[str, Any] = {"client_ref": client_ref, GLOBAL: bucket(client_id, GLOBAL)}
            if project is not None:
                row[PROJECT] = bucket(client_id, PROJECT, project)
            rows.append(row)
        return {"window_minutes": minutes, "clients": rows}

    return {"exposure status": exposure}
