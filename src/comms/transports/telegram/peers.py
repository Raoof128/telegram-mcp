"""The marked Telegram chat ID (5b-4 S2), shared by the bot and user transports.

A user and their private chat are both ``N``; a basic group is ``-N``; a channel or supergroup
is ``-100N``. A user and a group with the same raw number therefore never collide.
"""

from __future__ import annotations

__all__ = ["marked_chat_id"]

_MARKS = {"user": "", "private": "", "group": "-", "channel": "-100"}


def marked_chat_id(platform_identity: str) -> str:
    kind, sep, number = platform_identity.partition(":")
    if not sep or kind not in _MARKS or not number.isdigit() or not number.isascii():
        raise ValueError("unrecognised telegram peer")
    if int(number) == 0:
        raise ValueError("unrecognised telegram peer")
    return _MARKS[kind] + str(int(number))
