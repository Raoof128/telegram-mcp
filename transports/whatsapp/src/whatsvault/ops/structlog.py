"""Content-free structured logging (5x-A, ledger #57). event() returns a log record but
REFUSES any field that could carry WhatsApp message content, so logs never leak bodies."""

_FORBIDDEN_KEYS = {"text", "body", "caption", "message", "text_original", "display_text", "subject"}


class ContentInLogError(Exception):
    pass


def event(fields: dict) -> dict:
    for k, v in fields.items():
        if k in _FORBIDDEN_KEYS:
            raise ContentInLogError(f"log field {k!r} may carry message content")
        if isinstance(v, dict) and v.get("_wv_untrusted"):
            raise ContentInLogError("untrusted content wrapper in log field")
    return dict(fields)
