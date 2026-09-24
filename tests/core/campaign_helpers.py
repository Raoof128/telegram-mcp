"""Shared builders for campaign-core scenario tests (tests only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from comms.core.campaigns import directory as d
from comms.core.campaigns import drafts
from tests.core import schema_fixtures as fx

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def person(
    conn: Any, phone: str | None = None, tg: str | None = None
) -> tuple[str, dict[str, str]]:
    rcp = d.add_recipient(conn, now=NOW)
    pts = {}
    if phone:
        pts["wa"] = d.add_contact_point(conn, rcp, "whatsapp", phone, normalize=fx.wa, now=NOW)
    if tg:
        pts["tg"] = d.add_contact_point(conn, rcp, "telegram", tg, normalize=fx.tg, now=NOW)
    return rcp, pts


def ready(
    conn: Any,
    targets: dict,
    transports: frozenset[str] = frozenset({"whatsapp"}),
    body: str = "Happy Nowruz",
) -> str:
    cmp = drafts.create_campaign(conn, "T", now=NOW)
    drafts.set_content(conn, cmp, canonical=body, now=NOW)
    drafts.set_targets(conn, cmp, targets, transports, now=NOW)
    drafts.validate(conn, cmp, now=NOW)
    return cmp


def job_states(conn: Any, cmp: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT i.identity, j.state FROM delivery_jobs j JOIN delivery_identities i ON i.id = j.identity_id"
        " JOIN campaigns c ON c.current_generation_id = j.generation_id WHERE c.ref = ?",
        (cmp,),
    ).fetchall()
    return dict(rows)


def campaign_state(conn: Any, cmp: str) -> tuple[str, str | None]:
    return conn.execute("SELECT lifecycle, summary FROM campaigns WHERE ref = ?", (cmp,)).fetchone()
