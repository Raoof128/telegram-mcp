"""MCP presentation: redaction + untrusted-content wrapping (spec §5.3/§5.8, #22).

Every WhatsApp-/remotely-controlled string is returned inside an untrusted wrapper
so a downstream model treats it as attacker-controllable data, never instructions.
Full wa_ids never leave the boundary. display_text always equals the original.

Note on wamids (#22): a WhatsApp `wamid` base64-decodes to a structure carrying
the counterparty E.164 number in the clear, so emitting one raw would defeat
mask_wa_id. References are emitted as opaque, deterministic handles instead —
correlatable across results, but carrying no recoverable identifier."""

import hashlib


def _g(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def mask_wa_id(wa_id):
    if not wa_id:
        return None
    digits = "".join(c for c in str(wa_id) if c.isdigit())
    tail = digits[-4:] if len(digits) >= 4 else digits
    return "••••" + tail


def opaque_ref(value):
    """Stable, non-reversible handle for an identifier that must not be emitted."""
    if not value:
        return None
    return "wref_" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def untrusted(text):
    if text is None:
        return None
    return {"_wv_untrusted": True, "text": text}


def contact_ref(row):
    if row is None:
        return None
    return {
        "contact_id": _g(row, "id"),
        "display_name": untrusted(_g(row, "display_name")),
        "push_name": untrusted(_g(row, "push_name")),
        "wa_tail": mask_wa_id(_g(row, "wa_id")),
    }


def message_view(msg_row, contact_row):
    return {
        "message_id": _g(msg_row, "id"),
        "conversation_id": _g(msg_row, "conversation_id"),
        "direction": _g(msg_row, "direction"),
        "ts_lower_ms": _g(msg_row, "ts_lower_ms"),
        "ts_upper_ms_exclusive": _g(msg_row, "ts_upper_ms_exclusive"),
        "ts_precision": _g(msg_row, "ts_precision"),
        "delivery_rank": _g(msg_row, "delivery_rank"),
        "reply_to_ref": opaque_ref(_g(msg_row, "reply_to_wamid")),
        "body": untrusted(_g(msg_row, "text_original")),
        "contact": contact_ref(contact_row),
    }
