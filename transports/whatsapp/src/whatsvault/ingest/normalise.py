"""Mac-side webhook fan-out + six-family normaliser (spec §2/§3.4, ledger #2/#42).

One Meta POST carries nested entry[].changes[].value.{messages[],statuses[]}. Fan-out
happens HERE, after decrypt, on the Mac — the edge Worker stays raw-body dumb. Each
atomic event gets its own family, dedupe key, and disposition. Timestamps are provider
SECONDS -> ms. window_eligible=1 ONLY for live MESSAGE_INBOUND. SYSTEM/HISTORY/UNKNOWN
write no domain row (they live in ingest_events only, #42)."""

import hashlib
import json

from . import dedupe

PROVIDER = "meta"


def split_webhook(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            pnid = (value.get("metadata") or {}).get("phone_number_id")
            contacts = {
                c.get("wa_id"): (c.get("profile") or {}).get("name") for c in (value.get("contacts") or [])
            }
            msgs = value.get("messages") or []
            stats = value.get("statuses") or []
            out.extend(
                {
                    "kind": "message",
                    "phone_number_id": pnid,
                    "raw": m,
                    "echo": bool(m.get("_wv_echo")),
                    "contact_name": contacts.get(m.get("from")),
                }
                for m in msgs
            )
            out.extend({"kind": "status", "phone_number_id": pnid, "raw": s} for s in stats)
            if not msgs and not stats:
                out.append({"kind": "unknown", "phone_number_id": pnid, "raw": value})
    return out


def classify(atom: dict) -> str:
    k = atom["kind"]
    if k == "message":
        return "MESSAGE_ECHO" if atom.get("echo") else "MESSAGE_INBOUND"
    if k == "status":
        return "MESSAGE_STATUS"
    if k == "history":
        return "HISTORY_EVENT"
    if k == "system":
        return "SYSTEM_EVENT"
    return "UNKNOWN_SUPPORTED"


def _text(m):
    return (m.get("text") or {}).get("body") if m.get("type") == "text" else None


def to_rows(atom: dict) -> dict:
    fam = classify(atom)
    if fam in ("MESSAGE_INBOUND", "MESSAGE_ECHO"):
        m = atom["raw"]
        ms = int(m.get("timestamp", 0)) * 1000
        echo = fam == "MESSAGE_ECHO"
        return {
            "family": fam,
            "contact": {"wa_id": m.get("from"), "name": atom.get("contact_name")},
            "message": {
                "wamid": m.get("id"),
                "from_wa_id": m.get("from"),
                "ts_lower_ms": ms,
                "ts_upper_ms_exclusive": ms + 1000,
                "ts_precision": "s",
                "type": m.get("type", "text"),
                "text_original": _text(m),
                "direction": "out" if echo else "in",
                "origin": "business_app_echo" if echo else "cloud_api",
                "window_eligible": 0 if echo else 1,
                "phone_number_id": atom.get("phone_number_id"),
            },
        }
    if fam == "MESSAGE_STATUS":
        s = atom["raw"]
        return {
            "family": fam,
            "status": {
                "wamid": s.get("id"),
                "status": s.get("status"),
                "provider_ts_ms": int(s.get("timestamp", 0)) * 1000,
                "recipient_id": s.get("recipient_id"),
            },
        }
    return {"family": fam}  # SYSTEM/HISTORY/UNKNOWN -> ingest_events only (#42)


def semantic_key(atom: dict) -> tuple[str, str]:
    fam = classify(atom)
    pnid = atom.get("phone_number_id") or ""
    if fam in ("MESSAGE_INBOUND", "MESSAGE_ECHO"):
        return fam, dedupe.message_key(PROVIDER, pnid, atom["raw"].get("id") or "")
    if fam == "MESSAGE_STATUS":
        s = atom["raw"]
        return fam, dedupe.status_key(
            PROVIDER,
            pnid,
            s.get("id") or "",
            s.get("status") or "",
            int(s.get("timestamp", 0)) * 1000,
            s.get("recipient_id"),
        )
    raw = json.dumps(atom.get("raw"), sort_keys=True, ensure_ascii=False)
    return fam, hashlib.sha256(("WHATSVAULT-DEDUPE-OTHER-V1" + raw).encode("utf-8")).hexdigest()
