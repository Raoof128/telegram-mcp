"""Family-specific, domain-tagged semantic dedupe keys (spec §3.8)."""

import hashlib

_MSG_DOMAIN = "WHATSVAULT-DEDUPE-MESSAGE-V1"
_STATUS_DOMAIN = "WHATSVAULT-DEDUPE-STATUS-V1"


def _sha(domain: str, *parts: str) -> str:
    h = hashlib.sha256()
    h.update(domain.encode("utf-8"))
    for p in parts:
        b = p.encode("utf-8")
        h.update(len(b).to_bytes(4, "big"))
        h.update(b)
    return h.hexdigest()


def message_key(provider: str, phone_number_id: str, wamid: str) -> str:
    return _sha(_MSG_DOMAIN, provider, phone_number_id, wamid)


def status_key(
    provider: str, phone_number_id: str, wamid: str, status: str, provider_ts_ms: int, recipient_id
) -> str:
    rid = "\x00none" if recipient_id is None else recipient_id
    return _sha(_STATUS_DOMAIN, provider, phone_number_id, wamid, status, str(provider_ts_ms), rid)
