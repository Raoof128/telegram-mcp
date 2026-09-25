"""The marked Telegram chat ID (5b-4 S2), shared by the bot and user transports.

A user and their private chat are both ``N``; a basic group is ``-N``; a channel or supergroup
is ``-(10**12 + N)`` (the ``-100…`` form), exactly Telethon's ``utils.get_peer_id`` and the Bot
API's chat ids. A user and a group with the same raw number therefore never collide.
``unmark_chat_id`` is the inverse, to the MTProto peer kind the adapter resolves.
"""

from __future__ import annotations

__all__ = ["marked_chat_id", "unmark_chat_id"]

_CHANNEL_BASE = 10**12
_KINDS = {"user": "user", "private": "user", "group": "chat", "channel": "channel"}


def _positive(text: str) -> int:
    if not text.isascii() or not text.isdigit() or int(text) == 0:
        raise ValueError("unrecognised telegram peer")
    return int(text)


def marked_chat_id(platform_identity: str) -> str:
    kind, sep, number = platform_identity.partition(":")
    if not sep or kind not in _KINDS:
        raise ValueError("unrecognised telegram peer")
    raw = _positive(number)
    peer = _KINDS[kind]
    if peer == "user":
        return str(raw)
    if peer == "chat":
        return str(-raw)
    return str(-(_CHANNEL_BASE + raw))


def unmark_chat_id(marked: str) -> tuple[str, int]:
    negative = marked.startswith("-")
    value = _positive(marked[1:] if negative else marked)
    if not negative:
        return "user", value
    if value > _CHANNEL_BASE:
        return "channel", value - _CHANNEL_BASE
    return "chat", value
