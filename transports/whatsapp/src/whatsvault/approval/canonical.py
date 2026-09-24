"""Canonical WHATSVAULT-DRAFT-DECISION-V1 encoding (spec §6, ledger #10).

Length-prefixed binary: domain-separation prefix + version + per-field
uint32be(len)||bytes in a FIXED order. Absent optionals are a zero-length slot
(never omitted) so the byte layout is unambiguous. target_message_wamid is a
DISTINCT slot from reply_to_wamid (#10) — a read-receipt/mark-read target is never
overloaded onto the reply field. The iPhone reproduces these bytes exactly; the
sender recomputes them from the draft and verifies the P-256 signature over them."""

import hashlib

VERSION = 1
DOMAIN = b"WHATSVAULT-DRAFT-DECISION-V1\n"
_ATTACH_DOMAIN = b"WHATSVAULT-ATTACHMENTS-V1\n"

_FIELDS = [
    ("decision", "str"),
    ("draft_id", "str"),
    ("account_id", "str"),
    ("phone_number_id", "str"),
    ("recipient_wa_id", "str"),
    ("body_sha256", "bytes"),
    ("kind", "str"),
    ("template_id", "str"),
    ("template_params_sha256", "bytes"),
    ("reply_to_wamid", "str"),
    ("target_message_wamid", "str"),
    ("attachments_digest", "bytes"),
    ("nonce", "bytes"),
    ("created_at_ms", "u64"),
    ("expires_at_ms", "u64"),
    ("device_id", "str"),
]


def _field_bytes(typ, v) -> bytes:
    if v is None:
        return b""
    if typ == "str":
        return v.encode("utf-8")
    if typ == "bytes":
        return bytes(v)
    return int(v).to_bytes(8, "big")  # u64


def encode(fields: dict) -> bytes:
    out = bytearray(DOMAIN)
    out += VERSION.to_bytes(2, "big")
    for name, typ in _FIELDS:
        b = _field_bytes(typ, fields.get(name))
        out += len(b).to_bytes(4, "big")
        out += b
    return bytes(out)


def attachments_digest(items) -> bytes:
    h = hashlib.sha256()
    h.update(_ATTACH_DOMAIN)
    for i, it in enumerate(items or []):
        for part in (str(i), it.get("content_sha256", ""), it.get("mime", ""), str(it.get("size", 0))):
            pb = part.encode("utf-8")
            h.update(len(pb).to_bytes(4, "big"))
            h.update(pb)
    return h.digest()
