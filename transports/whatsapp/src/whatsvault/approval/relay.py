"""Approval relay (spec §6, ledger #14). Seals draft detail to the device agreement
key (Tunnel carries ciphertext, INV-DEVICE-SEAL) and accepts signed approval envelopes
idempotently. Before occupying the UNIQUE(draft_id,device_id,decision,nonce) slot it
runs a cheap STRUCTURAL check (known+ACTIVE device, 64-byte signature, valid decision,
existing draft) — defence-in-depth (#14), NOT a claimed-closed hole: it writes no
authoritative APPROVED state, and the sender re-verifies the signature and policy."""

import json

from .. import ids
from ..crypto import device_seal
from . import devices


class RelayRejected(Exception):
    pass


def sealed_draft_detail(control_conn, draft_id, device_id) -> bytes:
    agree_pub = devices.active_agreement_key(control_conn, device_id)
    if agree_pub is None:
        raise RelayRejected("device_has_no_active_agreement_key")
    row = control_conn.execute(
        "SELECT recipient_wa_id, body_bytes, nonce FROM drafts WHERE id=?", (draft_id,)
    ).fetchone()
    if not row:
        raise RelayRejected("unknown_draft")
    detail = json.dumps(
        {
            "draft_id": draft_id,
            "recipient_wa_id": row[0],
            "body": bytes(row[1]).decode("utf-8") if row[1] is not None else None,
            "nonce": bytes(row[2]).hex() if row[2] is not None else None,
        }
    ).encode("utf-8")
    return device_seal.seal(agree_pub, detail, aad=draft_id.encode("utf-8"))


def accept_envelope(control_conn, envelope_bytes) -> str:
    env = json.loads(envelope_bytes)
    dev = control_conn.execute(
        "SELECT status FROM approval_devices WHERE id=?", (env.get("device_id"),)
    ).fetchone()
    if not dev or dev[0] != "ACTIVE":
        raise RelayRejected("unknown_or_inactive_device")
    sig = bytes.fromhex(env["signature_hex"]) if env.get("signature_hex") else b""
    if len(sig) != 64:
        raise RelayRejected("bad_signature_length")
    if env.get("decision") not in ("APPROVE", "REJECT"):
        raise RelayRejected("bad_decision")
    if not control_conn.execute("SELECT 1 FROM drafts WHERE id=?", (env.get("draft_id"),)).fetchone():
        raise RelayRejected("unknown_draft")
    nonce = bytes.fromhex(env["nonce_hex"]) if env.get("nonce_hex") else None
    existing = control_conn.execute(
        "SELECT approval_id FROM approvals WHERE draft_id=? AND device_id=? AND decision=? AND nonce=?",
        (env["draft_id"], env["device_id"], env["decision"], nonce),
    ).fetchone()
    if existing:
        return existing[0]
    approval_id = ids.new_id("apv")
    control_conn.execute(
        "INSERT INTO approvals(approval_id, draft_id, device_id, decision, signature, envelope, "
        "received_at_ms, nonce) VALUES(?,?,?,?,?,?,?,?)",
        (approval_id, env["draft_id"], env["device_id"], env["decision"], sig, envelope_bytes, None, nonce),
    )
    control_conn.commit()
    return approval_id
