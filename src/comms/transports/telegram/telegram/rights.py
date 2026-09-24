"""The account's own standing in one chat, provider-neutral (comms v0.3 Task C17).

The adapter builds it from ``channels.getParticipant(self)`` for channels and supergroups, and
from ``messages.getFullChat`` for basic groups, so capability discovery sees one shape.
``rights`` are the MTProto ``ChatAdminRights`` flags the account holds (every one for the
creator); ``denied`` are the ``ChatBannedRights`` flags in force for it (its own restriction
plus the chat's defaults, which do not bind admins).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["SelfRights"]


@dataclass(frozen=True)
class SelfRights:
    kind: Literal["chat", "megagroup", "broadcast"]
    status: Literal["creator", "admin", "member", "restricted", "left", "banned"]
    rights: frozenset[str] = frozenset()
    denied: frozenset[str] = frozenset()
    is_forum: bool = False
