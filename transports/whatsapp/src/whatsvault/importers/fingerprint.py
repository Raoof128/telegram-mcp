"""Evidence fingerprints over ORIGINAL content (spec §3.9).

Never fold Yeh/Kaf/digits here — search normalisation is a separate, disposable
concern. content_fingerprint distinguishes byte-distinct originals; import_fingerprint
adds conversation/time-bucket/sender/occurrence so identical repeated lines get
distinct identities (via occurrence_index) yet re-imports dedupe deterministically."""

import hashlib

CONTENT_VERSION = 1


def _lp(*chunks: bytes) -> bytes:
    out = bytearray()
    for c in chunks:
        out += len(c).to_bytes(4, "big") + c
    return bytes(out)


def content_fingerprint(message_type: str, original_text: str | None) -> str:
    body = b"" if original_text is None else original_text.encode("utf-8")
    blob = b"WHATSVAULT-CONTENT-V1\n" + _lp(message_type.encode("utf-8"), body)
    return hashlib.sha256(blob).hexdigest()


def import_fingerprint(
    fingerprint_version: int,
    conversation_key: str,
    ts_bucket: int,
    sender_key: str,
    message_type: str,
    content_fp: str,
    occurrence_index: int,
) -> str:
    blob = b"WHATSVAULT-IMPORT-FP-V1\n" + _lp(
        fingerprint_version.to_bytes(4, "big"),
        conversation_key.encode("utf-8"),
        ts_bucket.to_bytes(8, "big", signed=True),
        sender_key.encode("utf-8"),
        message_type.encode("utf-8"),
        content_fp.encode("ascii"),
        occurrence_index.to_bytes(4, "big"),
    )
    return hashlib.sha256(blob).hexdigest()
