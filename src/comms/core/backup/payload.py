"""The ``comms-backup/v1`` plaintext payload and its account binding (comms v0.3 Task B25).

A backup carries what cannot be rebuilt from providers: the directory (delivery identities
included), locations, audiences, campaign *metadata* (title, target refs, options,
schedule) and non-secret settings, bound to this installation and its provider identities:

    binding = SHA-256("comms-backup-binding/v1\\0" ‖ JCS({installation_ref, providers}))

It never carries a campaign body, a generation, a job payload, an attempt, a provider event,
the audit chain or any key or credential. The payload is JCS; it is age-encrypted before it
leaves the daemon (Task B27), so identities appear in plaintext only here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from comms.core import domains
from comms.core.canonical import jcs_dumps
from comms.core.installation import installation_ref

__all__ = ["SCHEMA", "account_binding", "build_payload"]

SCHEMA = domains.BACKUP_SCHEMA.rstrip(b"\0").decode()
ProviderIdentities = Mapping[str, str]  # e.g. {"telegram_user": "4242", "meta_waba": "…"}


def account_binding(installation: str, providers: ProviderIdentities) -> str:
    body = jcs_dumps({"installation_ref": installation, "providers": dict(providers)})
    return hashlib.sha256(domains.BACKUP_BINDING + body).hexdigest()


def _rows(conn: Any, sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _directory(conn: Any) -> dict[str, Any]:
    return {
        "locations": _rows(
            conn, "SELECT ref, name, enabled, created_at FROM locations ORDER BY ref"
        ),
        "recipients": _rows(conn, "SELECT ref, enabled, created_at FROM recipients ORDER BY ref"),
        "destinations": _rows(
            conn,
            "SELECT d.ref, l.ref AS location_ref, d.transport, d.platform_identity, i.identity,"
            " d.display_name, d.capabilities, d.enabled, d.disabled_at, d.created_at"
            " FROM destinations d JOIN locations l ON l.id = d.location_id"
            " JOIN delivery_identities i ON i.id = d.identity_id ORDER BY d.ref",
        ),
        "contact_points": _rows(
            conn,
            "SELECT c.ref, r.ref AS recipient_ref, c.transport, c.platform_identity, i.identity,"
            " c.enabled, c.opted_out_at, c.disabled_at, c.created_at"
            " FROM contact_points c JOIN recipients r ON r.id = c.recipient_id"
            " JOIN delivery_identities i ON i.id = c.identity_id ORDER BY c.ref",
        ),
        "location_members": _rows(
            conn,
            "SELECT l.ref AS location_ref, r.ref AS recipient_ref FROM location_members m"
            " JOIN locations l ON l.id = m.location_id JOIN recipients r ON r.id = m.recipient_id"
            " ORDER BY 1, 2",
        ),
        "audiences": _rows(conn, "SELECT ref, name, created_at FROM audiences ORDER BY ref"),
        "audience_members": _rows(
            conn,
            "SELECT a.ref AS audience_ref, l.ref AS location_ref, x.ref AS member_audience_ref,"
            " d.ref AS destination_ref, r.ref AS recipient_ref FROM audience_members m"
            " JOIN audiences a ON a.id = m.audience_id"
            " LEFT JOIN locations l ON l.id = m.member_location_id"
            " LEFT JOIN audiences x ON x.id = m.member_audience_id"
            " LEFT JOIN destinations d ON d.id = m.member_destination_id"
            " LEFT JOIN recipients r ON r.id = m.member_recipient_id ORDER BY 1, 2, 3, 4, 5",
        ),
    }


def _campaigns(conn: Any) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT c.ref, c.title, c.lifecycle, c.targets, c.options, c.created_at, c.updated_at,"
        " CASE WHEN c.lifecycle = 'SCHEDULED' THEN g.send_at END AS scheduled_for"
        " FROM campaigns c LEFT JOIN generations g ON g.id = c.current_generation_id ORDER BY c.ref",
    )


def build_payload(conn: Any, providers: ProviderIdentities, *, now: datetime) -> bytes:
    installation = installation_ref(conn, now=now)
    return jcs_dumps(
        {
            "schema": SCHEMA,
            "installation_ref": installation,
            "providers": dict(providers),
            "binding": account_binding(installation, providers),
            "directory": _directory(conn),
            "campaigns": _campaigns(conn),
            "settings": {},
        }
    )
