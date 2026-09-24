"""Raw-SQL builders for comms.db tests (comms 5b-4). Tests only; never imported by src."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from comms.core import refs
from comms.core.storage.db import open_comms_db
from comms.core.storage.migrations import MIGRATIONS, migrate
from comms.transports.telegram.peers import marked_chat_id
from comms.transports.whatsapp.numbers import e164

KEY = bytes(range(32))
T0 = "2026-09-24T00:00:00.000000Z"


def migrated(tmp_path: Path, name: str = "comms.db") -> Any:
    conn = open_comms_db(tmp_path / name, KEY)
    migrate(conn, MIGRATIONS)
    return conn


def _insert(conn: Any, sql: str, params: tuple) -> int:
    return int(conn.execute(sql, params).lastrowid)


def location(conn: Any, *, enabled: int = 1) -> tuple[int, str]:
    ref = refs.mint("location")
    return _insert(
        conn,
        "INSERT INTO locations (ref, name, enabled, created_at) VALUES (?,?,?,?)",
        (ref, "L", enabled, T0),
    ), ref


def recipient(conn: Any, *, enabled: int = 1) -> tuple[int, str]:
    ref = refs.mint("recipient")
    return _insert(
        conn, "INSERT INTO recipients (ref, enabled, created_at) VALUES (?,?,?)", (ref, enabled, T0)
    ), ref


def identity(conn: Any, transport: str, value: str) -> int:
    return _insert(
        conn,
        "INSERT INTO delivery_identities (transport, identity) VALUES (?,?)",
        (transport, value),
    )


def destination(
    conn: Any, location_id: int, identity_id: int, *, enabled: int = 1, transport: str = "telegram"
) -> tuple[int, str]:
    ref = refs.mint("destination")
    return _insert(
        conn,
        "INSERT INTO destinations (ref, location_id, transport, platform_identity,"
        " identity_id, display_name, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (ref, location_id, transport, "raw", identity_id, "D", enabled, T0),
    ), ref


def contact_point(
    conn: Any, recipient_id: int, identity_id: int, transport: str, *, enabled: int = 1
) -> tuple[int, str]:
    ref = refs.mint("contact_point")
    return _insert(
        conn,
        "INSERT INTO contact_points (ref, recipient_id, transport, platform_identity,"
        " identity_id, enabled, created_at) VALUES (?,?,?,?,?,?,?)",
        (ref, recipient_id, transport, "raw", identity_id, enabled, T0),
    ), ref


def campaign(conn: Any, *, lifecycle: str = "DRAFT") -> tuple[int, str]:
    ref = refs.mint("campaign")
    return _insert(
        conn,
        "INSERT INTO campaigns (ref, title, lifecycle, content, targets, options,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (ref, "T", lifecycle, "{}", "{}", "{}", T0, T0),
    ), ref


def generation(
    conn: Any,
    campaign_id: int,
    *,
    status: str = "active",
    current: bool = True,
    summary: str = "IN_PROGRESS",
    send_at: str = T0,
) -> tuple[int, str]:
    ref = refs.mint("generation")
    gid = _insert(
        conn,
        "INSERT INTO generations (ref, campaign_id, created_at, send_at, content,"
        " snapshot_digest, status) VALUES (?,?,?,?,?,?,?)",
        (ref, campaign_id, T0, send_at, "{}", "0" * 64, status),
    )
    if current:
        conn.execute(
            "UPDATE campaigns SET current_generation_id = ?, summary = ? WHERE id = ?",
            (gid, summary, campaign_id),
        )
    return gid, ref


def job(
    conn: Any,
    generation_id: int,
    identity_id: int,
    transport: str,
    *,
    state: str = "PENDING",
    payload: bytes | None = b"payload",
    attempt_count: int = 0,
) -> tuple[int, str]:
    ref = refs.mint("job")
    digest = hashlib.sha256(payload).hexdigest() if payload is not None else None
    skip = None if payload is not None else "platform_ineligible"
    key = hashlib.sha256(ref.encode()).hexdigest()
    return _insert(
        conn,
        "INSERT INTO delivery_jobs (ref, generation_id, transport, identity_id,"
        " idempotency_key, payload, payload_digest, skip_reason, state, attempt_count)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            ref,
            generation_id,
            transport,
            identity_id,
            key,
            payload,
            digest,
            skip,
            state,
            attempt_count,
        ),
    ), ref


def origin(conn: Any, job_id: int, endpoint_ref: str, path: tuple[str, ...] | None = None) -> int:
    from comms.core.canonical import jcs_dumps

    chain = list(path) if path is not None else [endpoint_ref]
    return _insert(
        conn,
        "INSERT INTO job_origins (job_id, endpoint_ref, path) VALUES (?,?,?)",
        (job_id, endpoint_ref, jcs_dumps(chain).decode()),
    )


def attempt(
    conn: Any,
    job_id: int,
    attempt_no: int = 1,
    *,
    outcome: str | None = None,
    provider_message_ref: str | None = None,
) -> tuple[int, str]:
    ref = refs.mint("attempt")
    finished = T0 if outcome is not None else None
    return _insert(
        conn,
        "INSERT INTO delivery_attempts (ref, job_id, attempt_no, started_at,"
        " finished_at, outcome, provider_message_ref) VALUES (?,?,?,?,?,?,?)",
        (ref, job_id, attempt_no, T0, finished, outcome, provider_message_ref),
    ), ref


def world(conn: Any) -> dict[str, Any]:
    """One campaign in SENDING with one active generation and one WhatsApp PENDING job."""
    _, _ = location(conn)
    rid, _ = recipient(conn)
    ident = identity(conn, "whatsapp", "+61400000001")
    cp_id, cp_ref = contact_point(conn, rid, ident, "whatsapp")
    cid, cref = campaign(conn, lifecycle="SENDING")
    gid, gref = generation(conn, cid)
    jid, jref = job(conn, gid, ident, "whatsapp")
    origin(conn, jid, cp_ref)
    return {
        "recipient": rid,
        "identity": ident,
        "contact_point": cp_id,
        "cp_ref": cp_ref,
        "campaign": cid,
        "campaign_ref": cref,
        "generation": gid,
        "generation_ref": gref,
        "job": jid,
        "job_ref": jref,
    }


def tg(raw: str) -> str:
    """Marked Telegram identity (S2): the production rule, one copy (comms v0.3 C16)."""
    return marked_chat_id(raw)


def wa(raw: str) -> str:
    """E.164 canonical form: the production rule, one copy (comms v0.3 C23)."""
    return e164(raw)
