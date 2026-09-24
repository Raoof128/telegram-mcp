"""Sender — permission-to-transmit transaction + §6.6 matrix (spec §6, ledger #12/#13).

The model has NO dispatch verb; an approval envelope drives this. The sender owns clock
trust (ClockGuard, #12), recomputes the canonical bytes from the draft and verifies the
P-256 signature, re-evaluates P1-P7, consumes the nonce (approval_nonces UNIQUE — the
replay gate) and opens the send_attempt, COMMITS, then POSTs (HTTP retries disabled).
recover_startup resolves crash-stranded SUBMITTING attempts to INDETERMINATE (#13)."""

import hashlib

import sqlcipher3

from .. import ids
from ..providers.fake_meta import ConnectFailed, TimeoutAfterSend
from . import canonical, capabilities, devices, policy, verify
from .clockguard import ClockUntrusted

# Columns _finish() may write. Interpolated as SQL identifiers, so allowlisted.
_FINISH_COLUMNS = frozenset({"wamid", "error_code"})

WINDOW_MS = 24 * 3600 * 1000


def _canonical_fields(draft, env):
    def _b(v):
        return bytes(v) if v is not None else None

    return {
        "decision": env["decision"],
        "draft_id": draft["id"],
        "account_id": draft["account_id"],
        "phone_number_id": draft["phone_number_id"],
        "recipient_wa_id": draft["recipient_wa_id"],
        "body_sha256": _b(draft["body_sha256"]),
        "kind": draft["kind"],
        "template_id": draft["template_id"],
        "template_params_sha256": _b(draft["template_params_sha256"]),
        "reply_to_wamid": draft["reply_to_wamid"],
        "target_message_wamid": draft["target_message_wamid"],
        "attachments_digest": _b(draft["attachments_digest"]),
        "nonce": _b(draft["nonce"]),
        "created_at_ms": draft["created_at_ms"],
        "expires_at_ms": draft["expires_at_ms"],
        "device_id": env["device_id"],
    }


def _window_open(control_conn, conversation_id, now_ms):
    row = control_conn.execute(
        "SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    last = row[0] if row else 0
    return last > 0 and now_ms < last + WINDOW_MS


def _deny(reason):
    return {"outcome": "DENIED", "reason": reason}


def execute_write(vault_conn, control_conn, provider, signed_envelope, clock_guard) -> dict:
    try:
        now = clock_guard.trusted_now()
    except ClockUntrusted:
        return {"outcome": "REFUSED", "reason": "CLOCK_UNTRUSTED"}

    env = signed_envelope
    draft = control_conn.execute("SELECT * FROM drafts WHERE id=?", (env["draft_id"],)).fetchone()
    if not draft:
        return _deny("UNKNOWN_DRAFT")
    if env["decision"] != "APPROVE":
        return _deny("NOT_APPROVED")
    signing_pub = devices.active_signing_key(control_conn, env["device_id"])
    if signing_pub is None:
        return _deny("DEVICE_INACTIVE")
    payload = canonical.encode(_canonical_fields(draft, env))
    if not verify.verify(payload, env["signature"], signing_pub):
        return _deny("SIGNATURE_INVALID")
    body = bytes(draft["body_bytes"]) if draft["body_bytes"] is not None else b""
    if draft["body_sha256"] is not None and hashlib.sha256(body).digest() != bytes(draft["body_sha256"]):
        return _deny("PAYLOAD_CHANGED")
    ctx = {
        "recipient_wa_id": draft["recipient_wa_id"],
        "kind": draft["kind"],
        "account_ok": True,
        "now_ms": now,
        "expires_at_ms": draft["expires_at_ms"],
        "device_active": True,
        "rate_ok": True,
        "recipient_is_group": False,
        "window_open": _window_open(control_conn, draft["conversation_id"], now),
    }
    pol = policy.evaluate(ctx, phase="send")
    if not pol.ok:
        return _deny(pol.failed[0])

    # permission-to-transmit: consume nonce + open attempt, COMMIT, then POST
    try:
        control_conn.execute(
            "INSERT INTO approval_nonces(nonce, consumed_by, consumed_at_ms) VALUES(?,?,?)",
            (bytes(env["nonce"]), env["draft_id"], now),
        )
    except sqlcipher3.IntegrityError:
        return _deny("APPROVAL_ALREADY_CONSUMED")
    attempt_id = ids.new_id("atm")
    idem = hashlib.sha256(env["draft_id"].encode("utf-8") + bytes(env["nonce"])).hexdigest()
    control_conn.execute(
        "INSERT INTO send_attempts(id, draft_id, idempotency_key, state, created_at_ms, updated_at_ms) "
        "VALUES(?,?,?,'SUBMITTING',?,?)",
        (attempt_id, env["draft_id"], idem, now, now),
    )
    control_conn.commit()

    def _finish(state, **cols):
        unknown = set(cols) - _FINISH_COLUMNS
        if unknown:  # identifiers are interpolated, so they must be allowlisted
            raise ValueError(f"unknown send_attempts column(s): {sorted(unknown)}")
        sets = "".join(f", {k}=:{k}" for k in cols)
        control_conn.execute(
            f"UPDATE send_attempts SET state=:st, updated_at_ms=:now{sets} WHERE id=:id",
            {"st": state, "now": now, "id": attempt_id, **cols},
        )
        control_conn.commit()

    try:
        result = provider.send_text(
            phone_number_id=draft["phone_number_id"],
            recipient_wa_id=draft["recipient_wa_id"],
            body=body.decode("utf-8"),
        )
    except TimeoutAfterSend:
        _finish("INDETERMINATE")
        return {"outcome": "INDETERMINATE", "attempt_id": attempt_id}
    except ConnectFailed:
        _finish("FAILED", error_code="connect_fail")
        return {"outcome": "FAILED", "attempt_id": attempt_id}
    if result["outcome"] == "SUBMITTED":
        _finish("SUBMITTED", wamid=result.get("wamid"))
        return {"outcome": "SUBMITTED", "wamid": result.get("wamid"), "attempt_id": attempt_id}
    _finish("FAILED", error_code=result.get("error_code"))
    return {"outcome": "FAILED", "attempt_id": attempt_id}


def recover_startup(control_conn, now_ms) -> dict:
    cur = control_conn.execute(
        "UPDATE send_attempts SET state='INDETERMINATE', updated_at_ms=? WHERE state='SUBMITTING'", (now_ms,)
    )
    control_conn.commit()
    return {"recovered": cur.rowcount}


def mark_read(vault_conn, control_conn, provider, *, conversation_id, wamid, account_id, now_ms) -> dict:
    """Bind the target BEFORE consuming a grant (ledger #9): the wamid must exist, belong
    to conversation_id, be inbound, and match the account. Only then is a MARK_READ
    capability consumed (a typing indicator is a different action, not authorised here)."""
    m = vault_conn.execute(
        "SELECT conversation_id, direction FROM messages WHERE wamid=? AND account_id=?", (wamid, account_id)
    ).fetchone()
    if not m:
        return {"outcome": "DENIED", "reason": "UNKNOWN_TARGET"}
    if m["conversation_id"] != conversation_id:
        return {"outcome": "DENIED", "reason": "TARGET_CONVERSATION_MISMATCH"}
    if m["direction"] != "in":
        return {"outcome": "DENIED", "reason": "NOT_INBOUND"}
    if not capabilities.verify_and_consume(control_conn, "MARK_READ", conversation_id, now_ms):
        return {"outcome": "DENIED", "reason": "AUTHORIZATION_MISSING"}
    provider.mark_read(wamid=wamid)
    return {"outcome": "OK"}
