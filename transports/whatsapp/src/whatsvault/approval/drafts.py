"""Draft preparation (spec §6, ledger #11). prepare() runs the SHARED policy engine at
prepare, mints a 32-byte nonce, sets expiry, and writes a PENDING_APPROVAL draft — it
never approves or sends (no dispatch verb). Idempotent by body hash within a
conversation. The sender re-runs the same policy authoritatively at send."""

import hashlib
import os

from .. import ids
from . import canonical, policy

DEFAULT_TTL_MS = 15 * 60 * 1000


class DraftRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def prepare(
    control_conn,
    *,
    conversation_id,
    account_id,
    phone_number_id,
    recipient_wa_id,
    text,
    kind="text",
    now_ms,
    window_open,
    expires_at_ms=None,
) -> dict:
    ctx = {
        "recipient_wa_id": recipient_wa_id,
        "kind": kind,
        "account_ok": True,
        "now_ms": now_ms,
        "expires_at_ms": now_ms + 1,
        "device_active": True,
        "rate_ok": True,
        "recipient_is_group": False,
        "window_open": window_open,
    }
    result = policy.evaluate(ctx, phase="prepare")
    if not result.ok:
        raise DraftRefused(result.failed[0])
    body = text.encode("utf-8")
    bsha = hashlib.sha256(body).digest()
    existing = control_conn.execute(
        "SELECT id FROM drafts WHERE conversation_id=? AND body_sha256=? AND state='PENDING_APPROVAL'",
        (conversation_id, bsha),
    ).fetchone()
    if existing:
        return {"draft_id": existing[0], "reused": True}
    draft_id = ids.new_id("drf")
    control_conn.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, recipient_wa_id, "
        "body_bytes, body_sha256, kind, attachments_digest, nonce, created_at_ms, expires_at_ms, "
        "created_by, state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'mcp', 'PENDING_APPROVAL')",
        (
            draft_id,
            conversation_id,
            account_id,
            phone_number_id,
            recipient_wa_id,
            body,
            bsha,
            kind,
            canonical.attachments_digest([]),
            os.urandom(32),
            now_ms,
            expires_at_ms or (now_ms + DEFAULT_TTL_MS),
        ),
    )
    control_conn.commit()
    return {"draft_id": draft_id, "reused": False}
