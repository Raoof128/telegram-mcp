"""MTProto capability discovery across group kinds (comms v0.3 Task C17; P §9–12; A25).

The adapter's ``self_rights`` gives one ``SelfRights`` shape for basic groups and for channels
and supergroups; this module maps it to a state per P §11 capability, so the two group kinds
differ only where Telegram does (the admin log is channel-only; migration is basic-group-only).
The user reads history and searches wherever it can read (P §12). A private chat needs no
lookup. ``group.create`` is account-level, so it follows the session, not the destination.
Snapshots are advisory; the provider's answer to a mutation is final (A25).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderTarget
from comms.transports.telegram.capabilities import TELEGRAM_CAPABILITIES
from comms.transports.telegram.peers import unmark_chat_id
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.rights import SelfRights

__all__ = ["UserCapability"]

ACTOR = "telegram_user"
LOOKUP_TIMEOUT_S = 10.0
_SESSION_STATE = {
    "AUTH_REQUIRED": S.NOT_CONFIGURED,
    "SESSION_REVOKED": S.ACCOUNT_INELIGIBLE,
    "ACCOUNT_UNAVAILABLE": S.ACCOUNT_INELIGIBLE,
}
_LOOKUP_STATE = {
    "NOT_ACCESSIBLE": S.NOT_AUTHORIZED,
    "FLOOD_WAIT": S.TEMPORARILY_UNAVAILABLE,
    "TELEGRAM_UNAVAILABLE": S.TEMPORARILY_UNAVAILABLE,
    "DEADLINE_EXCEEDED": S.TEMPORARILY_UNAVAILABLE,
}
_SEND = frozenset({C.MESSAGE_SEND, C.MESSAGE_EDIT, C.MESSAGE_FORWARD})
_PRIVATE = _SEND | {C.MESSAGE_DELETE, C.MESSAGE_PIN, C.HISTORY_READ, C.HISTORY_SEARCH}
_ROSTER = frozenset({C.MEMBER_LIST, C.MEMBER_GET, C.ADMIN_LIST})
_RIGHT = {
    C.MESSAGE_DELETE: "delete_messages",
    C.MEMBER_REMOVE: "ban_users",
    C.MEMBER_BAN: "ban_users",
    C.MEMBER_UNBAN: "ban_users",
    C.MEMBER_RESTRICT: "ban_users",
    C.CHAT_SET_PERMISSIONS: "ban_users",
    C.ADMIN_PROMOTE: "add_admins",
    C.ADMIN_DEMOTE: "add_admins",
    C.INVITE_CREATE: "invite_users",
    C.INVITE_EDIT: "invite_users",
    C.INVITE_REVOKE: "invite_users",
    C.INVITE_LIST: "invite_users",
    C.JOIN_REQUEST_LIST: "invite_users",
    C.JOIN_REQUEST_APPROVE: "invite_users",
    C.JOIN_REQUEST_REJECT: "invite_users",
    C.CHAT_SET_TITLE: "change_info",
    C.CHAT_SET_DESCRIPTION: "change_info",
    C.CHAT_SET_PHOTO: "change_info",
    C.TOPIC_CREATE: "manage_topics",
    C.TOPIC_EDIT: "manage_topics",
    C.TOPIC_CLOSE: "manage_topics",
    C.TOPIC_REOPEN: "manage_topics",
}
_TOPICS = frozenset({C.TOPIC_LIST, C.TOPIC_CREATE, C.TOPIC_EDIT, C.TOPIC_CLOSE, C.TOPIC_REOPEN})
_SPECIAL = frozenset(
    {
        C.GROUP_CREATE,
        C.GROUP_DELETE,
        C.GROUP_MIGRATE,
        C.ADMIN_LOG_READ,
        C.MESSAGE_PIN,
        C.MEMBER_ADD,
        C.HISTORY_READ,
        C.HISTORY_SEARCH,
        C.TOPIC_LIST,
    }
)
assert set(TELEGRAM_CAPABILITIES) == _SEND | _ROSTER | set(_RIGHT) | _SPECIAL  # one rule each


class RightsSession(Protocol):
    def readiness(self) -> str | None: ...

    async def self_rights(self, peer_type: str, peer_id: int, *, timeout: float) -> SelfRights: ...


Runner = Callable[[Coroutine[Any, Any, SelfRights]], SelfRights]


class UserCapability:
    def __init__(
        self, session: RightsSession, *, run: Runner, clock: Callable[[], datetime]
    ) -> None:
        self._session, self._run, self._clock = session, run, clock

    def __repr__(self) -> str:
        return "UserCapability(<redacted>)"

    def snapshot(self, actor: str, destination: ProviderTarget) -> CapabilitySnapshot:
        if actor != ACTOR or destination.actor != ACTOR:
            raise ValueError("not a telegram_user destination")
        states = self._states(*unmark_chat_id(destination.identity))
        return CapabilitySnapshot(
            ACTOR, destination.destination_ref, states, timeutil.iso(self._clock())
        )

    def _states(self, peer_type: str, peer_id: int) -> dict[C, S]:
        unusable = _SESSION_STATE.get(self._session.readiness() or "")
        if unusable is not None:
            return dict.fromkeys(TELEGRAM_CAPABILITIES, unusable)
        if peer_type == "user":
            return {
                c: S.AVAILABLE if c in _PRIVATE | {C.GROUP_CREATE} else S.UNAVAILABLE
                for c in TELEGRAM_CAPABILITIES
            }
        try:
            view = self._run(
                self._session.self_rights(peer_type, peer_id, timeout=LOOKUP_TIMEOUT_S)
            )
        except GatewayError as failed:
            state = _LOOKUP_STATE.get(failed.code, S.UNKNOWN)
            return {c: S.AVAILABLE if c is C.GROUP_CREATE else state for c in TELEGRAM_CAPABILITIES}
        return {c: _state(c, view) for c in TELEGRAM_CAPABILITIES}


def _state(cap: C, view: SelfRights) -> S:
    if cap is C.GROUP_CREATE:
        return S.AVAILABLE
    if view.status in ("left", "banned"):
        return S.NOT_AUTHORIZED
    admin = view.status in ("creator", "admin")

    def has(right: str) -> S:
        granted = view.status == "creator" or (view.status == "admin" and right in view.rights)
        return S.AVAILABLE if granted else S.NOT_AUTHORIZED

    def member_may(right: str) -> bool:
        return view.kind != "broadcast" and not admin and right not in view.denied

    if cap in (C.HISTORY_READ, C.HISTORY_SEARCH):
        return S.AVAILABLE
    if cap in _SEND:
        if view.kind == "broadcast":
            return has("post_messages")
        return S.AVAILABLE if admin or "send_messages" not in view.denied else S.NOT_AUTHORIZED
    if cap in _ROSTER:
        return S.AVAILABLE if admin or view.kind != "broadcast" else S.NOT_AUTHORIZED
    if cap is C.MESSAGE_PIN:
        return S.AVAILABLE if member_may("pin_messages") else has("pin_messages")
    if cap is C.MEMBER_ADD:
        return S.AVAILABLE if member_may("invite_users") else has("invite_users")
    if cap is C.ADMIN_LOG_READ:
        if view.kind == "chat":
            return S.UNAVAILABLE
        return S.AVAILABLE if admin else S.NOT_AUTHORIZED
    if cap is C.GROUP_DELETE:
        return S.AVAILABLE if view.status == "creator" else S.NOT_AUTHORIZED
    if cap is C.GROUP_MIGRATE:
        if view.kind != "chat":
            return S.UNAVAILABLE
        return S.AVAILABLE if view.status == "creator" else S.NOT_AUTHORIZED
    if cap in _TOPICS:
        if not view.is_forum:
            return S.UNAVAILABLE
        return S.AVAILABLE if cap is C.TOPIC_LIST else has(_RIGHT[cap])
    return has(_RIGHT[cap])
