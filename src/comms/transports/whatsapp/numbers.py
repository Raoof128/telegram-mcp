"""The WhatsApp delivery identity: the E.164 number (5b-4 R3), one copy."""

from __future__ import annotations

__all__ = ["e164"]


def e164(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isascii() and ch.isdigit())
    if not raw.strip().startswith("+") or not 8 <= len(digits) <= 15:
        raise ValueError("unrecognised number")
    return "+" + digits
