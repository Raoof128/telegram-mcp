"""A campaign's WhatsApp template binding (comms v0.3 Task C23; A23).

The binding names one approved template by ``(name, language, schema_version)`` with its
literal body parameters. The freeze copies it into each ``DeliveryIntent``; the transport
freezes it into the payload with the parameter digests, and a template that is no longer
available at claim time is skipped, never swapped for another. Bindable while the campaign is
a draft or ready, never after it is frozen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.campaigns.drafts import LifecycleError, load
from comms.core.storage.db import write_tx

__all__ = ["bind_template", "template_binding"]

_NAME = re.compile(r"\A[a-z0-9_]{1,512}\Z")
_LANGUAGE = re.compile(r"\A[a-z]{2,3}(_[A-Z]{2})?\Z")


def bind_template(
    conn: Any,
    cmp: str,
    *,
    name: str,
    language: str,
    schema_version: int,
    parameters: Sequence[str],
    now: datetime,
) -> None:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError("template name refused")
    if not isinstance(language, str) or not _LANGUAGE.match(language):
        raise ValueError("template language refused")
    if type(schema_version) is not int or schema_version < 1:
        raise ValueError("template schema version refused")
    if isinstance(parameters, (str, bytes)) or not all(
        isinstance(p, str) and 1 <= len(p) <= 1024 for p in parameters
    ):
        raise ValueError("template parameters refused")
    with write_tx(conn):
        campaign = load(conn, cmp)
        if campaign["lifecycle"] not in ("DRAFT", "READY"):
            raise LifecycleError("campaign is frozen")
        conn.execute(
            "INSERT INTO template_bindings (campaign_id, name, language, schema_version, parameters, bound_at)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (campaign_id) DO UPDATE SET name = excluded.name,"
            " language = excluded.language, schema_version = excluded.schema_version,"
            " parameters = excluded.parameters, bound_at = excluded.bound_at",
            (
                campaign["id"],
                name,
                language,
                schema_version,
                json.dumps(list(parameters)),
                timeutil.iso(now),
            ),
        )


def template_binding(conn: Any, campaign_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT name, language, schema_version, parameters FROM template_bindings WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "name": row[0],
        "language": row[1],
        "schema_version": row[2],
        "parameters": json.loads(row[3]),
    }
